"""Notewise 필기를 새 PDF 배경 위로 옮긴다.

``.notewise`` 는 ZIP 컨테이너이고, 노트 색인과 각 페이지는 Base64로 감싼 protobuf 메시지다.
필기 객체는 페이지 메시지 안에 그대로 두고, 배경 PDF와 그것을 가리키는 참조(페이지 ID, PDF
id, 쪽 번호, 정렬 키)만 다시 쓴다.

쪽을 더하거나 지우거나 사용자가 확인한 순서로 바꾸면 페이지 ID가 새로 필요하므로 SDOCX 쪽과
달리 아카이브를 다시 만든다. 자동 매칭은 여전히 원본 순서를 보존하고, 수동 재정렬은 공통
``PagePlan`` 검증을 통과한 경우에만 적용한다.
"""
from __future__ import annotations

import base64
import os
import secrets
import struct
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo

from PIL import Image

from .alignment import Alignment, render_comparison
from .notewise_ink import render_notewise_ink
from .notewise_proto import NotewiseTransferError, encode_field, iter_fields
from .page_match import MatchResult
from .page_plan import PagePlan
from .transfer_plan import (
    TransferInspection,
    build_background_pdf,
    build_planned_background_pdf,
    open_pdf,
    plan_transfer,
)


def _page_ids(note_payload: bytes) -> list[str]:
    message = _decode_message(note_payload, "노트")
    try:
        return [
            bytes(value).decode("ascii")
            for number, wire_type, value in iter_fields(message)
            if number == 4 and wire_type == 2
        ]
    except UnicodeDecodeError as exc:
        raise NotewiseTransferError("Notewise 페이지 ID를 해석할 수 없습니다.") from exc


def _replace_field_sequence(
    data: bytes, number: int, replacements: list[tuple[int, bytes | int]]
) -> bytes:
    output = bytearray()
    inserted = False
    for current, wire_type, value in iter_fields(data):
        if current == number:
            if not inserted:
                for replacement_wire, replacement in replacements:
                    output.extend(encode_field(number, replacement_wire, replacement))
                inserted = True
            continue
        output.extend(encode_field(current, wire_type, value))
    if not inserted:
        for replacement_wire, replacement in replacements:
            output.extend(encode_field(number, replacement_wire, replacement))
    return bytes(output)


def _patch_note(
    note_payload: bytes,
    page_ids: list[str],
    note_id: str,
    pdf_id: str,
    relation_id: str,
    title: str,
    pdf_filename: str,
) -> bytes:
    message = _decode_message(note_payload, "노트")
    output = bytearray()
    inserted_pages = False
    for number, wire_type, value in iter_fields(message):
        if number == 1:
            value = note_id.encode("ascii")
            wire_type = 2
        elif number == 2:
            value = title.encode("utf-8")
            wire_type = 2
        elif number == 4:
            if not inserted_pages:
                for page_id in page_ids:
                    output.extend(encode_field(4, 2, page_id.encode("ascii")))
                inserted_pages = True
            continue
        if number == 6 and wire_type == 2:
            value = _replace_field_sequence(
                bytes(value), 1, [(2, pdf_id.encode("ascii"))]
            )
            value = _replace_field_sequence(bytes(value), 4, [(0, len(page_ids))])
            value = _replace_field_sequence(
                bytes(value), 5, [(2, pdf_filename.encode("utf-8"))]
            )
        elif number == 11 and wire_type == 2:
            value = _replace_field_sequence(
                bytes(value), 1, [(2, relation_id.encode("ascii"))]
            )
            value = _replace_field_sequence(
                bytes(value), 2, [(2, title.encode("utf-8"))]
            )
        output.extend(encode_field(number, wire_type, value))
    if not inserted_pages:
        for page_id in page_ids:
            output.extend(encode_field(4, 2, page_id.encode("ascii")))
    # Notewise 내보내기는 Android의 Base64.DEFAULT 형식을 쓴다 — 76자마다 줄바꿈, 끝에도
    # 줄바꿈 하나. 이 표기를 그대로 지켜야 한다. 한 줄로 붙여 쓰면 일부 버전이 노트 색인을
    # 무시하고 아카이브에 담긴 순서대로 페이지를 복구해 쪽 순서가 뒤죽박죽이 된다.
    return base64.encodebytes(bytes(output))


def _patch_page(
    page_payload: bytes,
    page_id: str,
    pdf_id: str,
    relation_id: str,
    target_index: int,
    *,
    blank: bool = False,
) -> bytes:
    message = _decode_message(page_payload, "페이지")
    output = bytearray()
    wrote_order = False
    wrote_sort_key = False
    for number, wire_type, value in iter_fields(message):
        if number == 1:
            value = page_id.encode("ascii")
            wire_type = 2
            output.extend(encode_field(number, wire_type, value))
            if target_index > 0:
                output.extend(encode_field(2, 0, target_index))
            wrote_order = True
            continue
        elif number == 2:
            # 페이지 ID 바로 뒤에 새 쪽 번호로 다시 넣는다.
            continue
        elif number == 3 and wire_type == 2:
            value = _replace_field_sequence(
                bytes(value), 1, [(2, pdf_id.encode("ascii"))]
            )
            value = _replace_field_sequence(bytes(value), 2, [(0, target_index)])
        elif blank and number == 4:
            continue
        elif number == 11:
            value = relation_id.encode("ascii")
            wire_type = 2
        elif number == 7:
            value = struct.pack("<d", 1024.0 * (target_index + 1))
            wire_type = 1
            wrote_sort_key = True
        output.extend(encode_field(number, wire_type, value))
    if not wrote_order and target_index > 0:
        output.extend(encode_field(2, 0, target_index))
    if not wrote_sort_key:
        output.extend(encode_field(7, 1, struct.pack("<d", 1024.0 * (target_index + 1))))
    return base64.encodebytes(bytes(output))


def _decode_message(payload: bytes, label: str) -> bytes:
    try:
        return base64.b64decode(payload, validate=False)
    except ValueError as exc:
        raise NotewiseTransferError(f"{label} 메타데이터를 해석할 수 없습니다.") from exc


def _safe_members(archive: ZipFile) -> dict[str, ZipInfo]:
    members: dict[str, ZipInfo] = {}
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts:
            raise NotewiseTransferError("안전하지 않은 경로가 들어 있는 Notewise 파일입니다.")
        if info.flag_bits & 0x1:
            raise NotewiseTransferError("암호화된 Notewise 파일은 지원하지 않습니다.")
        if info.filename in members:
            raise NotewiseTransferError(f"중복 ZIP 항목이 있습니다: {info.filename}")
        members[info.filename] = info
    return members


@contextmanager
def _archive_context(source: Path):
    try:
        archive = ZipFile(source, "r")
    except BadZipFile as exc:
        raise NotewiseTransferError(f"Notewise 파일을 열 수 없습니다: {source.name}") from exc
    try:
        members = _safe_members(archive)
        pdf_names = [name for name in members if name.startswith("pdf/") and not name.endswith("/")]
        page_names = [name for name in members if name.startswith("page/") and not name.endswith("/")]
        if "note" not in members or len(pdf_names) != 1 or not page_names:
            raise NotewiseTransferError(
                "note, page/*, pdf/* 구조를 가진 Notewise 파일만 지원합니다."
            )
        ordered_ids = _page_ids(archive.read("note"))
        ordered_names = [f"page/{page_id}" for page_id in ordered_ids]
        if set(ordered_names) != set(page_names):
            raise NotewiseTransferError("노트의 페이지 목록과 page/* 항목이 일치하지 않습니다.")
        yield archive, members, pdf_names[0], ordered_names
    finally:
        archive.close()


def _page_stroke_count(payload: bytes) -> int:
    message = _decode_message(payload, "페이지")
    return sum(
        1
        for number, wire_type, _value in iter_fields(message)
        if number == 4 and wire_type == 2
    )


def inspect_notewise_transfer(
    source_notewise: str | Path,
    target_pdf: str | Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> TransferInspection:
    source = Path(source_notewise).expanduser().resolve()
    target = Path(target_pdf).expanduser().resolve()
    if source.suffix.lower() != ".notewise" or not source.is_file():
        raise NotewiseTransferError("필기가 들어 있는 .notewise 파일을 선택하세요.")
    if target.suffix.lower() != ".pdf" or not target.is_file():
        raise NotewiseTransferError("새 배경으로 사용할 PDF를 선택하세요.")

    if progress:
        progress("structure")
    with _archive_context(source) as (archive, _members, pdf_name, page_names):
        embedded_pdf = archive.read(pdf_name)
        stroke_counts = [_page_stroke_count(archive.read(name)) for name in page_names]
    mode, alignment, page_count, match = plan_transfer(
        embedded_pdf,
        target,
        source_label="Notewise 내장 PDF",
        error=NotewiseTransferError,
        progress=progress,
    )
    return TransferInspection(
        source_name=source.name,
        target_name=target.name,
        page_count=page_count,
        annotated_page_count=sum(count > 0 for count in stroke_counts),
        stroke_cache_count=sum(stroke_counts),
        embedded_pdf_name=pdf_name,
        target_size=target.stat().st_size,
        source_page_count=len(page_names),
        mode=mode,
        alignment=alignment,
        match=match,
    )


def _background_bytes(
    embedded_pdf: bytes,
    target: Path,
    mapping: list[int | None],
    alignment: Alignment | None,
) -> bytes:
    """Notewise에 넣을 배경 PDF 바이트를 만든다.

    페이지 크기와 여백이 그대로면 사용자의 PDF를 **바이트 하나 안 바꾸고** 넣는다. 다시
    그리면 필요도 없는 차이가 생기고, 원본 PDF를 그대로 보존한다는 약속도 깨진다.
    달라졌을 때만 원본 캔버스에 다시 앉힌 PDF를 만들어 넣는다.
    """
    if alignment is None:
        return target.read_bytes()
    source_document = open_pdf(embedded_pdf, "Notewise 내장 PDF", error=NotewiseTransferError)
    try:
        target_document = open_pdf(target, "대상 PDF", error=NotewiseTransferError)
    except Exception:
        source_document.close()
        raise
    with source_document, target_document:
        return build_background_pdf(
            source_document,
            target_document,
            mapping,
            alignment,
            reference_index=_reference_index(mapping),
            error=NotewiseTransferError,
        )


def _planned_background_bytes(
    embedded_pdf: bytes,
    target: Path,
    plan: PagePlan,
    alignment: Alignment | None,
) -> bytes:
    if alignment is None and all(
        slot.target_index == output_index
        for output_index, slot in enumerate(plan.slots)
    ):
        # 행 추가·대상 재정렬이 없으면 사용자가 고른 PDF 바이트를 그대로 보존한다.
        return target.read_bytes()
    source_document = open_pdf(
        embedded_pdf, "Notewise 내장 PDF", error=NotewiseTransferError
    )
    try:
        target_document = open_pdf(target, "대상 PDF", error=NotewiseTransferError)
    except Exception:
        source_document.close()
        raise
    with source_document, target_document:
        return build_planned_background_pdf(
            source_document,
            target_document,
            plan,
            alignment,
            error=NotewiseTransferError,
        )


def _reference_index(mapping: list[int | None]) -> int:
    """새로 끼어든 쪽이 크기를 빌려 쓸 원본 쪽. 짝이 있는 첫 쪽을 기준으로 삼는다."""
    return next((index for index in mapping if index is not None), 0)


def _copy_info(info: ZipInfo) -> ZipInfo:
    copied = ZipInfo(info.filename, info.date_time)
    copied.compress_type = info.compress_type
    copied.comment = info.comment
    copied.extra = info.extra
    copied.internal_attr = info.internal_attr
    copied.external_attr = info.external_attr
    copied.create_system = info.create_system
    copied.flag_bits = info.flag_bits & ~0x1
    return copied


def _validate_archive_structure(
    archive: ZipFile,
    pdf_name: str,
    page_names: list[str],
    expected_pdf: bytes,
) -> None:
    if archive.read(pdf_name) != expected_pdf:
        raise NotewiseTransferError("저장된 Notewise의 내장 PDF 검증에 실패했습니다.")
    note = _decode_message(archive.read("note"), "노트")
    note_fields = list(iter_fields(note))
    pdf_metadata = next(
        bytes(value) for number, wire, value in note_fields if number == 6 and wire == 2
    )
    metadata_fields = list(iter_fields(pdf_metadata))
    pdf_id = next(
        bytes(value).decode("ascii")
        for number, wire, value in metadata_fields
        if number == 1 and wire == 2
    )
    page_count = next(
        int(value) for number, wire, value in metadata_fields if number == 4 and wire == 0
    )
    if pdf_name != f"pdf/{pdf_id}":
        raise NotewiseTransferError("Notewise PDF ID와 ZIP 경로가 일치하지 않습니다.")
    if page_count != len(page_names):
        raise NotewiseTransferError("Notewise 메타데이터의 페이지 수가 일치하지 않습니다.")
    for expected_index, page_name in enumerate(page_names):
        page = _decode_message(archive.read(page_name), "페이지")
        page_fields = list(iter_fields(page))
        page_id = next(
            bytes(value).decode("ascii")
            for number, wire, value in page_fields
            if number == 1 and wire == 2
        )
        if page_name != f"page/{page_id}":
            raise NotewiseTransferError("Notewise 페이지 ID와 ZIP 경로가 일치하지 않습니다.")
        page_order = next(
            (int(value) for number, wire, value in page_fields
             if number == 2 and wire == 0),
            0,
        )
        sort_key_payload = next(
            bytes(value) for number, wire, value in page_fields
            if number == 7 and wire == 1
        )
        sort_key = struct.unpack("<d", sort_key_payload)[0]
        if page_order != expected_index or sort_key != 1024.0 * (expected_index + 1):
            raise NotewiseTransferError(
                f"Notewise {expected_index + 1}쪽의 정렬 정보가 일치하지 않습니다."
            )
        background = next(
            bytes(value) for number, wire, value in page_fields if number == 3 and wire == 2
        )
        background_fields = list(iter_fields(background))
        background_pdf_id = next(
            bytes(value).decode("ascii")
            for number, wire, value in background_fields
            if number == 1 and wire == 2
        )
        background_index = next(
            (int(value) for number, wire, value in background_fields
             if number == 2 and wire == 0),
            0,
        )
        if background_pdf_id != pdf_id or background_index != expected_index:
            raise NotewiseTransferError(
                f"Notewise {expected_index + 1}쪽의 PDF 참조가 일치하지 않습니다."
            )


def transfer_notewise_handwriting(
    source_notewise: str | Path,
    target_pdf: str | Path,
    output_notewise: str | Path,
    *,
    match_override: MatchResult | None = None,
    plan_override: PagePlan | None = None,
) -> dict:
    source = Path(source_notewise).expanduser().resolve()
    target = Path(target_pdf).expanduser().resolve()
    output = Path(output_notewise).expanduser().resolve()
    if output.suffix.lower() != ".notewise":
        output = output.with_suffix(".notewise")
    if output == source:
        raise NotewiseTransferError("원본 Notewise 파일을 덮어쓸 수 없습니다.")

    inspection = inspect_notewise_transfer(source, target)
    match = match_override or inspection.match
    plan = plan_override or PagePlan.from_match(
        match, inspection.source_page_count, inspection.page_count
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=output.parent, prefix=f".{output.stem}-", suffix=".tmp.notewise"
    )
    os.close(fd)
    temporary = Path(temp_name)
    try:
        with _archive_context(source) as (archive, _members, pdf_name, source_page_names):
            source_pages = [archive.read(name) for name in source_page_names]
            target_payload = _planned_background_bytes(
                archive.read(pdf_name), target, plan, inspection.alignment
            )
            blank_template = next(
                (payload for payload in source_pages if _page_stroke_count(payload) == 0),
                source_pages[0],
            )
            output_page_ids: list[str] = []
            output_pages: list[tuple[str, bytes]] = []
            note_id = secrets.token_urlsafe(18)
            new_pdf_id = secrets.token_urlsafe(18)
            relation_id = secrets.token_urlsafe(18)
            for output_index, slot in enumerate(plan.slots):
                source_index = slot.source_index
                page_id = secrets.token_urlsafe(18)
                if source_index is None:
                    payload = _patch_page(
                        blank_template,
                        page_id,
                        new_pdf_id,
                        relation_id,
                        output_index,
                        blank=True,
                    )
                else:
                    payload = _patch_page(
                        source_pages[source_index],
                        page_id,
                        new_pdf_id,
                        relation_id,
                        output_index,
                    )
                output_page_ids.append(page_id)
                output_pages.append((f"page/{page_id}", payload))
            patched_note = _patch_note(
                archive.read("note"),
                output_page_ids,
                note_id,
                new_pdf_id,
                relation_id,
                target.stem,
                target.name,
            )
            with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as result:
                result.comment = archive.comment
                wrote_pages = False
                for info in archive.infolist():
                    if info.filename.startswith("page/"):
                        if not wrote_pages:
                            for name, payload in output_pages:
                                page_info = _copy_info(info)
                                page_info.filename = name
                                result.writestr(page_info, payload)
                            wrote_pages = True
                        continue
                    if info.filename == pdf_name:
                        payload = target_payload
                    elif info.filename == "note":
                        payload = patched_note
                    else:
                        payload = archive.read(info)
                    output_info = _copy_info(info)
                    if info.filename == pdf_name:
                        output_info.filename = f"pdf/{new_pdf_id}"
                    result.writestr(output_info, payload)
        with _archive_context(temporary) as (archive, _members, pdf_name, page_names):
            if len(page_names) != len(plan.slots):
                raise NotewiseTransferError("저장된 Notewise의 페이지 수가 달라졌습니다.")
            _validate_archive_structure(archive, pdf_name, page_names, target_payload)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "path": str(output),
        "page_count": len(plan.slots),
        "annotated_page_count": inspection.annotated_page_count,
        "stroke_count": inspection.stroke_cache_count,
        "mode": inspection.mode,
        "new_page_count": sum(slot.source_index is None for slot in plan.slots),
        "source_only_count": sum(slot.target_index is None for slot in plan.slots),
    }


def preview_notewise_transfer(
    source_notewise: str | Path,
    target_pdf: str | Path,
    page_index: int,
    inspection: TransferInspection | None = None,
    *,
    source_index_override: int = -2,
) -> tuple[bytes, bytes, bytes, int]:
    """이전 배경과 새 배경, 그리고 그 위에 얹을 Notewise 필기 레이어를 그린다."""
    import pymupdf

    source = Path(source_notewise).expanduser().resolve()
    target = Path(target_pdf).expanduser().resolve()
    inspection = inspection or inspect_notewise_transfer(source, target)
    target_index = max(0, min(int(page_index), inspection.page_count - 1))
    if source_index_override == -2:
        source_index = next(
            (pair.source_index for pair in inspection.match.pairs
             if pair.target_index == target_index),
            None,
        )
    elif source_index_override < 0:
        source_index = None
    else:
        source_index = int(source_index_override)
    if source_index is not None and not 0 <= source_index < inspection.source_page_count:
        source_index = None

    with _archive_context(source) as (archive, _members, pdf_name, page_names):
        embedded_pdf = archive.read(pdf_name)
        page_payload = archive.read(page_names[source_index]) if source_index is not None else None
    with pymupdf.open(target) as new_document:
        if source_index is None:
            target_page = new_document[target_index]
            scale = min(900 / max(target_page.rect.width, target_page.rect.height), 3.0)
            after = target_page.get_pixmap(
                matrix=pymupdf.Matrix(scale, scale), alpha=False
            ).tobytes("png")
            with Image.open(BytesIO(after)) as background:
                blank = Image.new("RGB", background.size, "white")
                before_output = BytesIO()
                blank.save(before_output, format="PNG")
            before = before_output.getvalue()
        else:
            old_document = pymupdf.open(stream=embedded_pdf, filetype="pdf")
            try:
                before, after = render_comparison(
                    old_document,
                    new_document,
                    inspection.alignment,
                    source_index,
                    target_page_index=target_index,
                )
            finally:
                old_document.close()
    with Image.open(BytesIO(after)) as background:
        if page_payload is None:
            transparent = Image.new("RGBA", background.size, (0, 0, 0, 0))
            ink_output = BytesIO()
            transparent.save(ink_output, format="PNG")
            ink, stroke_count = ink_output.getvalue(), 0
        else:
            ink, stroke_count = render_notewise_ink(page_payload, background.size)
    return before, after, ink, stroke_count
