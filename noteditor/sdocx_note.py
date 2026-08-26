"""``pageIdInfo.dat`` 의 쪽 목록과 ``note.note`` 의 쪽수 의존 필드를 다룬다.

노트의 페이지 목록은 ``pageIdInfo.dat`` 하나에만 있다. ``note.note`` 에는 쪽 목록이 없고,
쪽수에 걸리는 값은 ``height`` 하나뿐이다. 두 실제 파일에서 확인한 공식은

    height = Σ(페이지 캔버스 높이) + 세로여백 × (쪽수 − 1)

이고 오차 없이 맞는다(56613 = 54563 + 41×50). 그래서 쪽을 더하거나 빼도 ``note.note`` 는
u32 하나만 고치면 된다.

각 쪽의 32바이트 블록은 계산하지 않는다. 그 값은 해당 ``.page`` 파일 안에 들어 있으므로
:func:`noteditor.sdocx_page.page_hash` 로 읽어 그대로 옮긴다.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

_HASH_SIZE = 32
_NOTE_METADATA_OFFSET = 0x0E


class SdocxNoteError(RuntimeError):
    pass


@dataclass(frozen=True)
class PageOrderEntry:
    uuid: str
    page_hash: bytes


@dataclass(frozen=True)
class PageOrder:
    file_hash: bytes
    entries: tuple[PageOrderEntry, ...]

    def to_bytes(self) -> bytes:
        if len(self.file_hash) != _HASH_SIZE:
            raise SdocxNoteError("pageIdInfo.dat 파일 해시는 32바이트여야 합니다.")
        payload = bytearray(self.file_hash)
        payload += struct.pack("<H", len(self.entries))
        for entry in self.entries:
            if len(entry.page_hash) != _HASH_SIZE:
                raise SdocxNoteError(f"쪽 해시는 32바이트여야 합니다: {entry.uuid}")
            payload += struct.pack("<H", len(entry.uuid))
            payload += entry.uuid.encode("utf-16le")
            payload += entry.page_hash
        return bytes(payload)


def read_page_order(blob: bytes) -> PageOrder:
    if len(blob) < _HASH_SIZE + 2:
        raise SdocxNoteError("pageIdInfo.dat 가 너무 짧습니다.")
    count = struct.unpack_from("<H", blob, _HASH_SIZE)[0]
    position = _HASH_SIZE + 2
    entries: list[PageOrderEntry] = []
    for _ in range(count):
        if position + 2 > len(blob):
            raise SdocxNoteError("pageIdInfo.dat 쪽 항목이 잘렸습니다.")
        chars = struct.unpack_from("<H", blob, position)[0]
        start = position + 2
        end = start + chars * 2
        if end + _HASH_SIZE > len(blob):
            raise SdocxNoteError("pageIdInfo.dat 쪽 항목이 잘렸습니다.")
        try:
            uuid = blob[start:end].decode("utf-16le")
        except UnicodeDecodeError as exc:
            raise SdocxNoteError("pageIdInfo.dat 쪽 UUID를 해석할 수 없습니다.") from exc
        entries.append(PageOrderEntry(uuid, blob[end:end + _HASH_SIZE]))
        position = end + _HASH_SIZE
    if position != len(blob):
        raise SdocxNoteError(
            f"pageIdInfo.dat 에 해석하지 못한 바이트가 남았습니다: {len(blob) - position}"
        )
    return PageOrder(file_hash=blob[:_HASH_SIZE], entries=tuple(entries))


@dataclass(frozen=True)
class NoteInfo:
    format_version: int
    width: int
    height: int
    horizontal_padding: int
    vertical_padding: int
    height_offset: int


def read_note(blob: bytes) -> NoteInfo:
    if len(blob) < 0x30:
        raise SdocxNoteError("note.note 가 너무 짧습니다.")
    position = _NOTE_METADATA_OFFSET
    format_version = struct.unpack_from("<I", blob, position)[0]
    position += 4
    chars = struct.unpack_from("<H", blob, position)[0]
    position += 2 + chars * 2                     # noteId
    position += 4 + 8 + 8                         # fileRevision, createdTime, modifiedTime
    if position + 20 > len(blob):
        raise SdocxNoteError("note.note 메타데이터가 잘렸습니다.")
    width, height, pad_x, pad_y = struct.unpack_from("<IIII", blob, position)
    return NoteInfo(
        format_version=format_version,
        width=width,
        height=height,
        horizontal_padding=pad_x,
        vertical_padding=pad_y,
        height_offset=position + 4,
    )


def note_height(page_heights: list[int], vertical_padding: int) -> int:
    """실제 파일 두 개에서 오차 없이 확인한 공식."""
    if not page_heights:
        raise SdocxNoteError("쪽이 하나도 없는 노트는 만들 수 없습니다.")
    return sum(page_heights) + vertical_padding * (len(page_heights) - 1)


def patch_note_height(blob: bytes, page_heights: list[int]) -> bytes:
    """쪽 구성이 바뀐 만큼 note.note 의 전체 높이를 다시 쓴다."""
    info = read_note(blob)
    patched = bytearray(blob)
    struct.pack_into(
        "<I", patched, info.height_offset, note_height(page_heights, info.vertical_padding)
    )
    if len(patched) != len(blob):
        raise SdocxNoteError("note.note 를 고치는 중 길이가 변했습니다.")
    return bytes(patched)
