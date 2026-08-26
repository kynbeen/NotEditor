"""Samsung Notes ``.page`` 파일에서 필기를 건드리지 않고 고칠 수 있는 부분만 다룬다.

필기 획은 가변길이 델타로 패킹돼 있어 다시 직렬화하면 조용히 깨진다. 그래서 이 모듈은 페이지를
**해석하지 않고**, 크기가 고정된 필드만 제자리에서 바꾼다:

- 배경 PDF의 쪽 번호 (``pdfDataList`` 의 ``pageIndex``, i32)
- 페이지 UUID (UTF-16LE 36자, 길이 불변)
- 캔버스 크기 (``pageWidth``/``pageHeight``, u32) — 획이 없는 빈 쪽에만 안전하다
- 파일 끝의 32바이트 무결성 블록

무결성 블록의 계산식은 모른다. 알 필요도 없다 — 그 값이 ``.page`` 파일 안(끝에서 58바이트)에
들어 있고 ``pageIdInfo.dat`` 이 같은 값을 들고 있으므로, 둘을 같이 옮기면 일관성이 유지된다.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

PAGE_FOOTER = b"Page for SAMSUNG S-Pen SDK"
_HASH_SIZE = 32
_HEADER_SIZE = 0x12
_UUID_OFFSET = 0x26
_CANVAS_OFFSET = 0x16

_MASK_DRAWN_RECT = 0x00000001
_MASK_TAG_LIST = 0x00000002
_MASK_TEMPLATE_URI = 0x00000004
_MASK_BG_IMAGE_ID = 0x00000008
_MASK_BG_IMAGE_MODE = 0x00000010
_MASK_BG_COLOR = 0x00000020
_MASK_BG_WIDTH = 0x00000040
_MASK_BG_ROTATION = 0x00000080
_MASK_PDF_DATA_LIST = 0x00000100
_MASK_TEMPLATE_TYPE = 0x00000200
_MASK_CANVAS_CACHE = 0x00000400
_MASK_IMPORTED_HEIGHT = 0x00000800
_MASK_RESERVED_1000 = 0x00001000
_KNOWN_MASK = (
    _MASK_DRAWN_RECT | _MASK_TAG_LIST | _MASK_TEMPLATE_URI | _MASK_BG_IMAGE_ID
    | _MASK_BG_IMAGE_MODE | _MASK_BG_COLOR | _MASK_BG_WIDTH | _MASK_BG_ROTATION
    | _MASK_PDF_DATA_LIST | _MASK_TEMPLATE_TYPE | _MASK_CANVAS_CACHE
    | _MASK_IMPORTED_HEIGHT | _MASK_RESERVED_1000
)


class SdocxPageError(RuntimeError):
    pass


@dataclass(frozen=True)
class PdfReference:
    """페이지가 어느 PDF의 몇 쪽을 배경으로 쓰는지."""

    file_id: int
    page_index: int
    entry_offset: int          # pdfDataList 첫 레코드의 파일 내 위치
    entry_count: int


@dataclass(frozen=True)
class PageInfo:
    uuid: str
    canvas_width: int
    canvas_height: int
    orientation: int
    format_version: int
    property_mask: int
    layer_offset: int
    property_offset: int
    property_end: int
    pdf: PdfReference | None
    page_hash: bytes

    @property
    def has_pdf_background(self) -> bool:
        return self.pdf is not None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SdocxPageError(message)


def _skip_utf16(blob: bytes, position: int) -> int:
    chars = struct.unpack_from("<H", blob, position)[0]
    return position + 2 + chars * 2


def _walk_properties(blob: bytes, mask: int, start: int) -> tuple[int, PdfReference | None]:
    """속성 블록을 순서대로 훑어 끝 위치와 PDF 참조를 돌려준다."""
    unknown = mask & ~_KNOWN_MASK
    _require(not unknown, f"모르는 페이지 속성 비트가 있습니다: 0x{unknown:08x}")

    position = start
    reference: PdfReference | None = None
    if mask & _MASK_DRAWN_RECT:
        position += 32                       # float64 4개
    if mask & _MASK_TAG_LIST:
        count = struct.unpack_from("<H", blob, position)[0]
        position += 2
        for _ in range(count):
            position = _skip_utf16(blob, position)
    if mask & _MASK_TEMPLATE_URI:
        position = _skip_utf16(blob, position)
    for bit in (_MASK_BG_IMAGE_ID, _MASK_BG_IMAGE_MODE, _MASK_BG_COLOR,
                _MASK_BG_WIDTH, _MASK_BG_ROTATION):
        if mask & bit:
            position += 4
    if mask & _MASK_PDF_DATA_LIST:
        count = struct.unpack_from("<H", blob, position)[0]
        entry_offset = position + 2
        _require(count >= 1, "PDF 배경 목록이 비어 있습니다.")
        file_id, page_index = struct.unpack_from("<ii", blob, entry_offset)
        reference = PdfReference(file_id, page_index, entry_offset, count)
        position = entry_offset + count * 24
    if mask & _MASK_TEMPLATE_TYPE:
        position += 4
    if mask & _MASK_CANVAS_CACHE:
        count, record_size = struct.unpack_from("<IH", blob, position)
        position += 6 + count * record_size
    if mask & _MASK_IMPORTED_HEIGHT:
        position += 4
    if mask & _MASK_RESERVED_1000:
        position += 4
    _require(position <= len(blob), "페이지 속성 블록이 파일 끝을 넘어갑니다.")
    return position, reference


def read_page(blob: bytes) -> PageInfo:
    """``.page`` 를 해석한다. 획 데이터는 건드리지 않고 위치만 알아낸다."""
    _require(len(blob) > _HEADER_SIZE + _HASH_SIZE + len(PAGE_FOOTER), "페이지 파일이 너무 짧습니다.")
    _require(blob.endswith(PAGE_FOOTER), "지원하지 않는 페이지 파일 형식입니다(꼬리표 없음).")

    layer_offset, property_offset = struct.unpack_from("<II", blob, 0)
    mask = struct.unpack_from("<I", blob, 0x0E)[0]
    orientation, canvas_width, canvas_height = struct.unpack_from("<III", blob, 0x12)

    position = _UUID_OFFSET
    chars = struct.unpack_from("<H", blob, position)[0]
    uuid = blob[position + 2:position + 2 + chars * 2].decode("utf-16le")
    position += 2 + chars * 2
    position += 8                              # modifiedTime
    format_version = struct.unpack_from("<I", blob, position)[0]

    _require(
        property_offset >= position + 8,
        "페이지 속성 블록 위치가 메타데이터와 겹칩니다.",
    )
    property_end, reference = _walk_properties(blob, mask, property_offset)
    # 속성 블록의 끝이 레이어 구간의 시작과 정확히 맞아야 해석이 옳다.
    _require(
        property_end == layer_offset,
        f"페이지 속성 블록 해석이 맞지 않습니다: 끝 {property_end}, 레이어 시작 {layer_offset}",
    )
    return PageInfo(
        uuid=uuid,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        orientation=orientation,
        format_version=format_version,
        property_mask=mask,
        layer_offset=layer_offset,
        property_offset=property_offset,
        property_end=property_end,
        pdf=reference,
        page_hash=blob[-(_HASH_SIZE + len(PAGE_FOOTER)):-len(PAGE_FOOTER)],
    )


def page_hash(blob: bytes) -> bytes:
    """``pageIdInfo.dat`` 이 들고 있는 것과 같은 32바이트 블록."""
    _require(blob.endswith(PAGE_FOOTER), "지원하지 않는 페이지 파일 형식입니다(꼬리표 없음).")
    return blob[-(_HASH_SIZE + len(PAGE_FOOTER)):-len(PAGE_FOOTER)]


def patch_page(
    blob: bytes,
    *,
    pdf_page_index: int | None = None,
    uuid: str | None = None,
    canvas: tuple[int, int] | None = None,
    new_page_hash: bytes | None = None,
) -> bytes:
    """고정 크기 필드만 제자리에서 바꾼다. 파일 길이는 절대 변하지 않는다."""
    info = read_page(blob)
    patched = bytearray(blob)

    if pdf_page_index is not None:
        _require(info.pdf is not None, "이 페이지에는 PDF 배경이 없습니다.")
        struct.pack_into("<i", patched, info.pdf.entry_offset + 4, pdf_page_index)

    if uuid is not None:
        _require(
            len(uuid) == len(info.uuid),
            f"페이지 UUID 길이가 달라 제자리 교체를 할 수 없습니다: {len(uuid)} != {len(info.uuid)}",
        )
        start = _UUID_OFFSET + 2
        patched[start:start + len(uuid) * 2] = uuid.encode("utf-16le")

    if canvas is not None:
        struct.pack_into("<II", patched, _CANVAS_OFFSET, int(canvas[0]), int(canvas[1]))

    if new_page_hash is not None:
        _require(len(new_page_hash) == _HASH_SIZE, "페이지 해시는 32바이트여야 합니다.")
        end = len(patched) - len(PAGE_FOOTER)
        patched[end - _HASH_SIZE:end] = new_page_hash

    result = bytes(patched)
    _require(len(result) == len(blob), "페이지를 고치는 중 길이가 변했습니다.")
    read_page(result)  # 고친 결과가 여전히 해석되는지 확인한다.
    return result
