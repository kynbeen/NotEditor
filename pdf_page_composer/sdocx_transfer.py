"""Samsung Notes SDOCX 필기 레이어를 새 PDF 배경으로 옮긴다.

SDOCX는 ZIP 컨테이너이며, 가져온 PDF는 ``mediaInfo.dat``의 bind id로 참조된다.
필기·형광펜 객체와 SPI 캐시는 그대로 보존하고 내장 PDF 바이트와 그 SHA-256만 교체한다.
페이지 좌표를 변환하지 않으므로 두 PDF의 페이지 수·크기·회전은 반드시 같아야 한다.

Samsung Notes는 ZIP 종료 기록(EOCD) **뒤에** ``Document for S-Pen SDK`` 로 끝나는 꼬리표를
덧붙이고, 각 엔트리에 자기만의 플래그 비트를 쓴다. 일반 ZIP 라이브러리로 다시 포장하면 이
바이트들이 사라져 Samsung Notes가 파일을 열지 못한다. 그래서 아카이브를 다시 만들지 않고
바뀌는 두 엔트리만 제자리에서 갈아 끼운다.
"""
from __future__ import annotations

import hashlib
import os
import struct
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from zipfile import BadZipFile, ZipFile, ZipInfo

from .alignment import Alignment, build_aligned_pdf, estimate_alignment, render_comparison

_LOCAL_HEADER = b"PK\x03\x04"
_CENTRAL_HEADER = b"PK\x01\x02"
_END_OF_CENTRAL = b"PK\x05\x06"
_ZIP32_LIMIT = 0xFFFFFFFF
_COPY_CHUNK = 1 << 20


class SdocxTransferError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaEntry:
    bind_id: int
    filename: str
    hash_offset: int
    file_hash: str


@dataclass(frozen=True)
class TransferInspection:
    source_name: str
    target_name: str
    page_count: int
    annotated_page_count: int
    stroke_cache_count: int
    embedded_pdf_name: str
    target_size: int
    mode: str = "exact"
    alignment: Alignment | None = None

    def as_dict(self) -> dict:
        return {
            "source_name": self.source_name,
            "target_name": self.target_name,
            "page_count": self.page_count,
            "annotated_page_count": self.annotated_page_count,
            "stroke_cache_count": self.stroke_cache_count,
            "embedded_pdf_name": self.embedded_pdf_name,
            "target_size": self.target_size,
            "mode": self.mode,
            "alignment": self.alignment.as_dict() if self.alignment else None,
        }


def parse_media_info(data: bytes) -> list[MediaEntry]:
    """Samsung ``mediaInfo.dat``의 엔트리와 해시 바이트 위치를 읽는다."""
    if len(data) < 10:
        raise SdocxTransferError("SDOCX mediaInfo.dat가 너무 짧습니다.")
    _format_version, entry_count = struct.unpack_from("<IH", data, 0)
    position = 6
    entries: list[MediaEntry] = []
    for _ in range(entry_count):
        if position + 4 > len(data):
            raise SdocxTransferError("SDOCX mediaInfo.dat 엔트리가 잘렸습니다.")
        body_size = struct.unpack_from("<I", data, position)[0]
        body_start = position + 4
        body_end = body_start + body_size
        if body_end > len(data) or body_size < 70:
            raise SdocxTransferError("SDOCX mediaInfo.dat 엔트리 크기가 올바르지 않습니다.")
        bind_id = struct.unpack_from("<I", data, body_start)[0]
        name_chars = struct.unpack_from("<H", data, body_start + 4)[0]
        name_start = body_start + 6
        name_end = name_start + name_chars * 2
        hash_start = name_end
        hash_end = hash_start + 64
        if hash_end > body_end:
            raise SdocxTransferError("SDOCX mediaInfo.dat 파일명 또는 해시가 잘렸습니다.")
        try:
            filename = data[name_start:name_end].decode("utf-16le")
            file_hash = data[hash_start:hash_end].decode("ascii")
        except UnicodeDecodeError as exc:
            raise SdocxTransferError("SDOCX mediaInfo.dat 문자열을 해석할 수 없습니다.") from exc
        if len(file_hash) != 64 or any(char not in "0123456789abcdefABCDEF" for char in file_hash):
            raise SdocxTransferError("SDOCX mediaInfo.dat SHA-256 값이 올바르지 않습니다.")
        entries.append(MediaEntry(bind_id, filename, hash_start, file_hash.lower()))
        position = body_end
    if data[position:position + 4] != b"EOFX":
        raise SdocxTransferError("지원하지 않는 SDOCX mediaInfo.dat 형식입니다.")
    return entries


def _safe_members(archive: ZipFile) -> dict[str, ZipInfo]:
    members: dict[str, ZipInfo] = {}
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts:
            raise SdocxTransferError("안전하지 않은 경로가 들어 있는 SDOCX입니다.")
        if info.flag_bits & 0x1:
            raise SdocxTransferError("암호화된 SDOCX는 지원하지 않습니다.")
        if info.filename in members:
            raise SdocxTransferError(f"중복 ZIP 엔트리가 있는 SDOCX입니다: {info.filename}")
        members[info.filename] = info
    return members


def _find_suffix(members: dict[str, ZipInfo], suffix: str) -> str:
    normalized = suffix.lower().replace("\\", "/")
    matches = [name for name in members if name.lower().replace("\\", "/").endswith(normalized)]
    if len(matches) != 1:
        raise SdocxTransferError(f"SDOCX에서 {suffix} 파일을 정확히 하나 찾을 수 없습니다.")
    return matches[0]


def _open_pdf(pdf: bytes | Path, label: str):
    """PDF를 열고 필기 이전에 쓸 수 없는 문서는 그 자리에서 거른다. 호출자가 닫는다."""
    import pymupdf

    try:
        if isinstance(pdf, Path):
            document = pymupdf.open(pdf)
        else:
            document = pymupdf.open(stream=pdf, filetype="pdf")
    except Exception as exc:
        raise SdocxTransferError(f"{label}를 읽을 수 없습니다: {exc}") from exc
    try:
        if document.needs_pass:
            raise SdocxTransferError(f"암호화된 {label}는 지원하지 않습니다.")
        if document.page_count < 1:
            raise SdocxTransferError(f"페이지가 없는 {label}입니다.")
    except Exception:
        document.close()
        raise
    return document


def _geometry(document) -> list[tuple[float, float, int]]:
    return [
        (round(float(page.rect.width), 3), round(float(page.rect.height), 3), int(page.rotation))
        for page in document
    ]


def _pdf_geometry(pdf: bytes | Path, label: str) -> list[tuple[float, float, int]]:
    document = _open_pdf(pdf, label)
    with document:
        return _geometry(document)


def _geometry_mismatches(
    source: list[tuple[float, float, int]], target: list[tuple[float, float, int]]
) -> list[int]:
    mismatches = []
    for index, (left, right) in enumerate(zip(source, target), start=1):
        same_size = abs(left[0] - right[0]) <= 0.5 and abs(left[1] - right[1]) <= 0.5
        if not same_size or left[2] != right[2]:
            mismatches.append(index)
    return mismatches


def _validate_geometry(source: list[tuple[float, float, int]], target: list[tuple[float, float, int]]) -> None:
    if len(source) != len(target):
        raise SdocxTransferError(
            f"페이지 수가 다릅니다: 필기 원본 {len(source)}쪽, 대상 PDF {len(target)}쪽"
        )
    mismatches = _geometry_mismatches(source, target)
    if mismatches:
        shown = ", ".join(map(str, mismatches[:8]))
        suffix = "…" if len(mismatches) > 8 else ""
        raise SdocxTransferError(
            f"페이지 크기 또는 회전이 다른 쪽이 있습니다: {shown}{suffix}. "
            "동일한 페이지 좌표계의 PDF만 필기를 정확히 옮길 수 있습니다."
        )


def _plan_transfer(embedded_pdf: bytes, target: Path) -> tuple[str, Alignment | None, int]:
    """그대로 넣을지(``exact``), 본문 기준으로 다시 앉힐지(``aligned``) 정한다."""
    source_document = _open_pdf(embedded_pdf, "SDOCX 내장 PDF")
    try:
        target_document = _open_pdf(target, "대상 PDF")
    except Exception:
        source_document.close()
        raise
    try:
        source_geometry = _geometry(source_document)
        target_geometry = _geometry(target_document)
        if len(source_geometry) != len(target_geometry):
            raise SdocxTransferError(
                f"페이지 수가 다릅니다: 필기 원본 {len(source_geometry)}쪽, "
                f"대상 PDF {len(target_geometry)}쪽"
            )
        same_geometry = not _geometry_mismatches(source_geometry, target_geometry)
        alignment = estimate_alignment(source_document, target_document)
    finally:
        source_document.close()
        target_document.close()

    if alignment is None:
        if not same_geometry:
            raise SdocxTransferError(
                "페이지 크기가 다른데 두 문서의 본문 영역을 찾지 못해 정렬 배율을 정할 수 없습니다. "
                "내용이 비어 있거나 스캔 품질이 낮은 문서일 수 있습니다."
            )
        return "exact", None, len(target_geometry)
    if same_geometry and not (alignment.improves and alignment.axes_agree):
        # 페이지 크기가 같고 본문 배치도 그대로면 사용자의 PDF를 바이트 그대로 넣는다.
        return "exact", None, len(target_geometry)
    return "aligned", alignment, len(target_geometry)


@dataclass(frozen=True)
class _ZipEntry:
    """중앙 디렉터리 레코드 원본 바이트와 위치를 그대로 들고 있는 ZIP 엔트리."""

    record: bytes
    name: str
    compress_type: int
    compress_size: int
    local_offset: int


def _read_zip_layout(handle: BinaryIO) -> tuple[list[_ZipEntry], bytes, bytes]:
    """엔트리 목록, EOCD 레코드, EOCD 뒤에 붙은 Samsung 꼬리표를 읽는다."""
    handle.seek(0, os.SEEK_END)
    total = handle.tell()
    window = min(total, 0xFFFF + 22)
    handle.seek(total - window)
    tail = handle.read(window)
    position = tail.rfind(_END_OF_CENTRAL)
    if position < 0:
        raise SdocxTransferError("SDOCX의 ZIP 종료 기록을 찾을 수 없습니다.")
    comment_length = struct.unpack_from("<H", tail, position + 20)[0]
    end_of_central = tail[position:position + 22 + comment_length]
    if len(end_of_central) != 22 + comment_length:
        raise SdocxTransferError("SDOCX의 ZIP 종료 기록이 잘렸습니다.")
    trailer = tail[position + 22 + comment_length:]
    entry_count, central_size, central_offset = struct.unpack_from("<HII", end_of_central, 10)
    if entry_count == 0xFFFF or _ZIP32_LIMIT in {central_size, central_offset}:
        raise SdocxTransferError("Zip64 형식의 SDOCX는 지원하지 않습니다.")

    handle.seek(central_offset)
    central = handle.read(central_size)
    if len(central) != central_size:
        raise SdocxTransferError("SDOCX의 ZIP 중앙 디렉터리가 잘렸습니다.")

    entries: list[_ZipEntry] = []
    position = 0
    for _ in range(entry_count):
        if central[position:position + 4] != _CENTRAL_HEADER:
            raise SdocxTransferError("SDOCX의 ZIP 중앙 디렉터리 구조가 올바르지 않습니다.")
        flag_bits, compress_type = struct.unpack_from("<HH", central, position + 8)
        compress_size = struct.unpack_from("<I", central, position + 20)[0]
        name_length, extra_length, comment_size = struct.unpack_from("<HHH", central, position + 28)
        local_offset = struct.unpack_from("<I", central, position + 42)[0]
        if flag_bits & 0x1:
            raise SdocxTransferError("암호화된 SDOCX는 지원하지 않습니다.")
        if flag_bits & 0x8:
            raise SdocxTransferError("데이터 서술자를 쓰는 SDOCX는 지원하지 않습니다.")
        if _ZIP32_LIMIT in {compress_size, local_offset}:
            raise SdocxTransferError("Zip64 형식의 SDOCX는 지원하지 않습니다.")
        name_start = position + 46
        end = name_start + name_length + extra_length + comment_size
        if end > len(central):
            raise SdocxTransferError("SDOCX의 ZIP 중앙 디렉터리 엔트리가 잘렸습니다.")
        name = central[name_start:name_start + name_length].decode(
            "utf-8" if flag_bits & 0x800 else "cp437"
        )
        entries.append(
            _ZipEntry(central[position:end], name, compress_type, compress_size, local_offset)
        )
        position = end
    if position != len(central):
        raise SdocxTransferError("SDOCX의 ZIP 중앙 디렉터리 길이가 맞지 않습니다.")
    return entries, end_of_central, trailer


def _compress_like(payload: bytes, compress_type: int) -> bytes:
    if compress_type == 0:
        return payload
    if compress_type == 8:
        compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
        return compressor.compress(payload) + compressor.flush()
    raise SdocxTransferError(f"지원하지 않는 압축 방식의 SDOCX 엔트리입니다: {compress_type}")


def _copy_bytes(reader: BinaryIO, writer: BinaryIO, length: int) -> None:
    remaining = length
    while remaining > 0:
        chunk = reader.read(min(_COPY_CHUNK, remaining))
        if not chunk:
            raise SdocxTransferError("SDOCX 엔트리 데이터가 잘렸습니다.")
        writer.write(chunk)
        remaining -= len(chunk)


def _rewrite_archive(source: Path, destination: Path, replacements: dict[str, bytes]) -> bytes:
    """바뀌는 엔트리만 갈아 끼우고 나머지는 압축 상태 그대로 복사한다.

    ZIP 헤더의 플래그 비트·타임스탬프·엔트리 순서와 EOCD 뒤의 Samsung 꼬리표를 그대로 두어야
    Samsung Notes가 결과 파일을 연다. 반환값은 보존한 꼬리표 바이트다.
    """
    pending = dict(replacements)
    with source.open("rb") as reader, destination.open("wb") as writer:
        entries, end_of_central, trailer = _read_zip_layout(reader)
        moved: dict[int, int] = {}
        patched: dict[str, tuple[int, int, int]] = {}
        for entry in sorted(entries, key=lambda item: item.local_offset):
            reader.seek(entry.local_offset)
            header = reader.read(30)
            if header[:4] != _LOCAL_HEADER:
                raise SdocxTransferError(f"SDOCX 엔트리 헤더가 올바르지 않습니다: {entry.name}")
            name_length, extra_length = struct.unpack_from("<HH", header, 26)
            header += reader.read(name_length + extra_length)
            moved[entry.local_offset] = writer.tell()
            payload = pending.pop(entry.name, None)
            if payload is None:
                writer.write(header)
                _copy_bytes(reader, writer, entry.compress_size)
                continue
            stored = _compress_like(payload, entry.compress_type)
            fields = (zlib.crc32(payload) & _ZIP32_LIMIT, len(stored), len(payload))
            local = bytearray(header)
            struct.pack_into("<III", local, 14, *fields)
            writer.write(bytes(local))
            writer.write(stored)
            patched[entry.name] = fields
        if pending:
            missing = ", ".join(sorted(pending))
            raise SdocxTransferError(f"SDOCX에서 교체할 엔트리를 찾지 못했습니다: {missing}")

        central_offset = writer.tell()
        for entry in entries:
            record = bytearray(entry.record)
            struct.pack_into("<I", record, 42, moved[entry.local_offset])
            if entry.name in patched:
                struct.pack_into("<III", record, 16, *patched[entry.name])
            writer.write(bytes(record))
        central_size = writer.tell() - central_offset
        if central_offset > _ZIP32_LIMIT or writer.tell() > _ZIP32_LIMIT:
            raise SdocxTransferError("결과 SDOCX가 4GB를 넘어 저장할 수 없습니다.")

        record = bytearray(end_of_central)
        struct.pack_into("<II", record, 12, central_size, central_offset)
        writer.write(bytes(record))
        writer.write(trailer)
    return trailer


def _read_trailer(path: Path) -> bytes:
    with path.open("rb") as handle:
        return _read_zip_layout(handle)[2]


def _archive_context(source_sdocx: Path):
    try:
        archive = ZipFile(source_sdocx, "r")
        members = _safe_members(archive)
        media_info_name = _find_suffix(members, "media/mediaInfo.dat")
        media_info = archive.read(media_info_name)
        entries = parse_media_info(media_info)
        pdf_entries = [entry for entry in entries if entry.filename.lower().endswith(".pdf")]
        if len(pdf_entries) != 1:
            archive.close()
            raise SdocxTransferError("내장 PDF가 정확히 하나인 Samsung Notes 파일만 지원합니다.")
        media_root = PurePosixPath(media_info_name).parent
        embedded_name = str(media_root / pdf_entries[0].filename)
        if embedded_name not in members:
            archive.close()
            raise SdocxTransferError(f"SDOCX 내장 PDF를 찾을 수 없습니다: {pdf_entries[0].filename}")
        return archive, members, media_info_name, media_info, pdf_entries[0], embedded_name
    except SdocxTransferError:
        raise
    except (BadZipFile, KeyError, OSError) as exc:
        raise SdocxTransferError(f"Samsung Notes 파일을 열 수 없습니다: {source_sdocx.name}") from exc


def _aligned_pdf_bytes(
    embedded_pdf: bytes, target: Path, alignment: Alignment, workspace: Path
) -> bytes:
    """대상 PDF를 원본 페이지 좌표계에 다시 앉힌 PDF 바이트를 만든다."""
    source_document = _open_pdf(embedded_pdf, "SDOCX 내장 PDF")
    try:
        target_document = _open_pdf(target, "대상 PDF")
    except Exception:
        source_document.close()
        raise

    handle, staged_name = tempfile.mkstemp(dir=workspace, prefix=".aligned-", suffix=".pdf")
    os.close(handle)
    staged = Path(staged_name)
    try:
        with source_document, target_document:
            build_aligned_pdf(source_document, target_document, alignment, staged)
            source_geometry = _geometry(source_document)
        # 다시 그린 PDF가 원본 페이지 좌표계와 정확히 같은지 확인한 뒤에만 내보낸다.
        _validate_geometry(source_geometry, _pdf_geometry(staged, "정렬한 PDF"))
        return staged.read_bytes()
    finally:
        staged.unlink(missing_ok=True)


def inspect_transfer(source_sdocx: str | Path, target_pdf: str | Path) -> TransferInspection:
    source = Path(source_sdocx).expanduser().resolve()
    target = Path(target_pdf).expanduser().resolve()
    if source.suffix.lower() != ".sdocx" or not source.is_file():
        raise SdocxTransferError("필기가 들어 있는 .sdocx 파일을 선택하세요.")
    if target.suffix.lower() != ".pdf" or not target.is_file():
        raise SdocxTransferError("필기를 옮길 대상 PDF 파일을 선택하세요.")

    archive, members, _media_info_name, _media_info, _pdf_entry, embedded_name = _archive_context(source)
    try:
        embedded_pdf = archive.read(embedded_name)
        page_sizes = [info.file_size for name, info in members.items() if name.lower().endswith(".page")]
        annotated_pages = sum(size > 358 for size in page_sizes)
        spi_count = sum(name.lower().endswith(".spi") for name in members)
    finally:
        archive.close()

    mode, alignment, page_count = _plan_transfer(embedded_pdf, target)

    return TransferInspection(
        source_name=source.name,
        target_name=target.name,
        page_count=page_count,
        annotated_page_count=annotated_pages,
        stroke_cache_count=spi_count,
        embedded_pdf_name=PurePosixPath(embedded_name).name,
        target_size=target.stat().st_size,
        mode=mode,
        alignment=alignment,
    )


def preview_transfer(
    source_sdocx: str | Path,
    target_pdf: str | Path,
    page_index: int,
    inspection: TransferInspection | None = None,
    max_side: int = 900,
) -> tuple[bytes, bytes]:
    """한 쪽의 (원본 배경, 옮긴 뒤 배경) PNG 쌍을 같은 크기로 렌더링한다.

    필기는 원본 배경 위에 그려져 있으므로, 두 배경의 본문이 겹치면 필기도 겹친다.
    저장하기 전에 사람이 눈으로 확인할 수 있는 유일한 지점이다.
    """
    source = Path(source_sdocx).expanduser().resolve()
    target = Path(target_pdf).expanduser().resolve()
    if inspection is None:
        inspection = inspect_transfer(source, target)

    archive, _members, _media_info_name, _media_info, _pdf_entry, embedded_name = _archive_context(source)
    try:
        embedded_pdf = archive.read(embedded_name)
    finally:
        archive.close()

    if not 0 <= page_index < inspection.page_count:
        raise SdocxTransferError(f"{inspection.page_count}쪽 문서에 없는 쪽 번호입니다: {page_index + 1}")

    source_document = _open_pdf(embedded_pdf, "SDOCX 내장 PDF")
    try:
        target_document = _open_pdf(target, "대상 PDF")
    except Exception:
        source_document.close()
        raise
    try:
        with source_document, target_document:
            return render_comparison(
                source_document, target_document, inspection.alignment, page_index, max_side
            )
    except SdocxTransferError:
        raise
    except Exception as exc:
        raise SdocxTransferError(f"미리보기를 만들 수 없습니다: {exc}") from exc


def transfer_handwriting(
    source_sdocx: str | Path,
    target_pdf: str | Path,
    output_sdocx: str | Path,
) -> dict:
    source = Path(source_sdocx).expanduser().resolve()
    target = Path(target_pdf).expanduser().resolve()
    output = Path(output_sdocx).expanduser().resolve()
    inspection = inspect_transfer(source, target)
    if output.suffix.lower() != ".sdocx":
        output = output.with_suffix(".sdocx")
    if output in {source, target}:
        raise SdocxTransferError("원본 파일을 덮어쓸 수 없습니다. 새 파일명으로 저장하세요.")
    output.parent.mkdir(parents=True, exist_ok=True)

    archive, members, media_info_name, media_info, pdf_entry, embedded_name = _archive_context(source)
    try:
        embedded_pdf = archive.read(embedded_name)
    finally:
        archive.close()

    if inspection.mode == "aligned" and inspection.alignment is not None:
        target_bytes = _aligned_pdf_bytes(
            embedded_pdf, target, inspection.alignment, output.parent
        )
    else:
        target_bytes = target.read_bytes()
    target_hash = hashlib.sha256(target_bytes).hexdigest()
    patched_media_info = bytearray(media_info)
    patched_media_info[pdf_entry.hash_offset:pdf_entry.hash_offset + 64] = target_hash.encode("ascii")

    handle, temporary_name = tempfile.mkstemp(
        dir=output.parent, prefix=f".{output.stem}-", suffix=".tmp.sdocx"
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        trailer = _rewrite_archive(
            source,
            temporary,
            {embedded_name: target_bytes, media_info_name: bytes(patched_media_info)},
        )
        if _read_trailer(temporary) != trailer:
            raise SdocxTransferError("저장된 SDOCX의 Samsung 꼬리표 검증에 실패했습니다.")

        with ZipFile(temporary, "r") as check:
            checked_members = _safe_members(check)
            if set(checked_members) != set(members):
                raise SdocxTransferError("저장된 SDOCX 엔트리 구성이 원본과 다릅니다.")
            if hashlib.sha256(check.read(embedded_name)).hexdigest() != target_hash:
                raise SdocxTransferError("저장된 SDOCX의 대상 PDF 검증에 실패했습니다.")
            checked_media = check.read(media_info_name)
            checked_pdf = next(
                entry for entry in parse_media_info(checked_media)
                if entry.filename == pdf_entry.filename
            )
            if checked_pdf.file_hash != target_hash:
                raise SdocxTransferError("저장된 SDOCX의 PDF 해시 검증에 실패했습니다.")
            for name, info in members.items():
                if name in {embedded_name, media_info_name} or info.is_dir():
                    continue
                checked = check.getinfo(name)
                if checked.file_size != info.file_size or checked.CRC != info.CRC:
                    raise SdocxTransferError(f"필기 데이터 보존 검증에 실패했습니다: {name}")

        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return {
        **inspection.as_dict(),
        "path": str(output),
        "size": output.stat().st_size,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "preserved_entry_count": len(members) - 2,
        "footer_size": len(trailer),
    }
