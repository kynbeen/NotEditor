"""쪽이 추가·삭제된 PDF를 기준으로 Samsung Notes 페이지 구성을 다시 만든다."""
from __future__ import annotations

import hashlib
import os
import secrets
import tempfile
import uuid as uuid_module
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Sequence
from zipfile import ZipFile

from .alignment import Alignment, estimate_alignment
from .ink_transform import canvas_transform
from .page_match import MatchResult
from .sdocx_note import PageOrder, PageOrderEntry, patch_note_height, read_note, read_page_order
from .sdocx_page import PageInfo, is_blank_page, patch_page, read_page
from .sdocx_ink import transform_page_ink
from .sdocx_transfer import (
    ArchiveAddition,
    SdocxTransferError,
    _archive_context,
    _find_suffix,
    _open_pdf,
    _read_trailer,
    _rewrite_archive,
    _safe_members,
    parse_media_info,
)


class SdocxRebuildError(SdocxTransferError):
    pass


@dataclass(frozen=True)
class _SourcePage:
    name: str
    blob: bytes
    info: PageInfo


@dataclass(frozen=True)
class _PdfSlot:
    page_name: str
    page_blob: bytes
    source_index: int | None
    target_index: int | None
    reference_source_index: int
    added: bool


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SdocxRebuildError(message)


def _page_name(root: PurePosixPath, page_uuid: str) -> str:
    filename = f"{page_uuid}.page"
    return filename if str(root) == "." else str(root / filename)


def _validate_match(
    result: MatchResult,
    source_count: int,
    target_count: int,
    excluded_sources: Sequence[int] = (),
    excluded_targets: Sequence[int] = (),
) -> None:
    """모든 쪽이 결과에 담겼거나 사용자가 명시적으로 뺀 것인지 확인한다.

    사용자가 뺀 쪽까지 세는 이유는, "빠졌다"와 "빼기로 했다"를 구분하지 못하면
    조용한 쪽 유실을 잡아 주던 이 검사가 무의미해지기 때문이다.
    """
    source = [pair.source_index for pair in result.pairs if pair.source_index is not None]
    target = [pair.target_index for pair in result.pairs if pair.target_index is not None]
    _require(
        sorted(source + list(excluded_sources)) == list(range(source_count)),
        "쪽 대응 계획은 원본 PDF의 모든 쪽을 한 번씩 담아야 합니다.",
    )
    _require(
        sorted(target + list(excluded_targets)) == list(range(target_count)),
        "쪽 대응 계획은 대상 PDF의 모든 쪽을 한 번씩 담아야 합니다.",
    )
    _require(
        all(pair.source_index is not None or pair.target_index is not None for pair in result.pairs),
        "양쪽 쪽 번호가 모두 비어 있는 매칭 항목이 있습니다.",
    )


def _same_page_geometry(left, right) -> bool:
    return (
        abs(float(left.rect.width) - float(right.rect.width)) <= 0.5
        and abs(float(left.rect.height) - float(right.rect.height)) <= 0.5
        and int(left.rotation) == int(right.rotation)
    )


def _choose_alignment(source_document, target_document, result: MatchResult) -> Alignment | None:
    pairs = [
        (pair.source_index, pair.target_index)
        for pair in result.matched_pairs
    ]
    same_geometry = all(
        _same_page_geometry(source_document[source], target_document[target])
        for source, target in pairs
    )
    alignment = estimate_alignment(source_document, target_document, pairs)
    if alignment is None:
        if not same_geometry:
            raise SdocxRebuildError(
                "페이지 크기가 다른데 공통 쪽의 본문 영역을 충분히 찾지 못해 정렬할 수 없습니다."
            )
        return None
    if same_geometry and not (alignment.improves and alignment.axes_agree):
        return None
    return alignment


def _read_source_pages(
    archive: ZipFile,
    members,
    order_name: str,
    pdf_bind_id: int,
    source_count: int,
) -> tuple[PageOrder, dict[int, _SourcePage], list[_SourcePage], _SourcePage | None]:
    order = read_page_order(archive.read(order_name))
    root = PurePosixPath(order_name).parent
    pdf_pages: dict[int, _SourcePage] = {}
    supplemental: list[_SourcePage] = []
    blank_templates: list[_SourcePage] = []
    for entry in order.entries:
        name = _page_name(root, entry.uuid)
        _require(name in members, f"pageIdInfo.dat가 가리키는 페이지 파일이 없습니다: {name}")
        blob = archive.read(name)
        info = read_page(blob)
        _require(info.uuid == entry.uuid, f"페이지 UUID와 파일명이 다릅니다: {name}")
        _require(info.page_hash == entry.page_hash, f"페이지 해시가 pageIdInfo.dat와 다릅니다: {name}")
        page = _SourcePage(name, blob, info)
        if info.pdf is None:
            supplemental.append(page)
            continue
        _require(info.pdf.file_id == pdf_bind_id, f"다른 PDF를 가리키는 페이지는 지원하지 않습니다: {name}")
        _require(0 <= info.pdf.page_index < source_count, f"PDF 쪽 번호가 범위를 벗어납니다: {name}")
        _require(info.pdf.page_index not in pdf_pages, f"같은 PDF 쪽을 가리키는 페이지가 둘 이상입니다: {name}")
        pdf_pages[info.pdf.page_index] = page
        if is_blank_page(blob):
            blank_templates.append(page)
    _require(
        set(pdf_pages) == set(range(source_count)),
        "원본 PDF의 각 쪽에 대응하는 .page 파일을 정확히 하나씩 찾을 수 없습니다.",
    )
    template = min(blank_templates, key=lambda page: len(page.blob), default=None)
    return order, pdf_pages, supplemental, template


def _new_uuid(existing: set[str], factory: Callable[[], str]) -> str:
    for _ in range(100):
        candidate = str(factory()).lower()
        if len(candidate) == 36 and candidate not in existing:
            existing.add(candidate)
            return candidate
    raise SdocxRebuildError("중복되지 않는 새 페이지 UUID를 만들 수 없습니다.")


def rebuild_handwriting(
    source_sdocx: str | Path,
    target_pdf: str | Path,
    output_sdocx: str | Path,
    match: MatchResult,
    *,
    uuid_factory: Callable[[], str] | None = None,
    hash_factory: Callable[[int], bytes] | None = None,
    mode: str = "rebuild",
    excluded_sources: Sequence[int] = (),
    excluded_targets: Sequence[int] = (),
) -> dict:
    """``match`` 순서대로 PDF와 페이지 목록을 재조립해 새 SDOCX를 저장한다."""
    from . import pdf as pymupdf

    source = Path(source_sdocx).expanduser().resolve()
    target = Path(target_pdf).expanduser().resolve()
    output = Path(output_sdocx).expanduser().resolve()
    if source.suffix.lower() != ".sdocx" or not source.is_file():
        raise SdocxRebuildError("필기가 들어 있는 .sdocx 파일을 선택하세요.")
    if target.suffix.lower() != ".pdf" or not target.is_file():
        raise SdocxRebuildError("필기를 옮길 대상 PDF 파일을 선택하세요.")
    if output.suffix.lower() != ".sdocx":
        output = output.with_suffix(".sdocx")
    if output in {source, target}:
        raise SdocxRebuildError("원본 파일을 덮어쓸 수 없습니다. 새 파일명으로 저장하세요.")
    output.parent.mkdir(parents=True, exist_ok=True)

    archive, members, media_info_name, media_info, pdf_entry, embedded_name = _archive_context(source)
    try:
        order_name = _find_suffix(members, "pageIdInfo.dat")
        note_name = _find_suffix(members, "note.note")
        embedded_pdf = archive.read(embedded_name)
        note_blob = archive.read(note_name)
        source_document = _open_pdf(embedded_pdf, "SDOCX 내장 PDF")
        try:
            target_document = _open_pdf(target, "대상 PDF")
        except Exception:
            source_document.close()
            raise
        try:
            _validate_match(
                match,
                source_document.page_count,
                target_document.page_count,
                excluded_sources,
                excluded_targets,
            )
            order, source_pages, supplemental, blank_template = _read_source_pages(
                archive,
                members,
                order_name,
                pdf_entry.bind_id,
                source_document.page_count,
            )
            if match.target_only:
                _require(
                    blank_template is not None,
                    "새 쪽을 만들 빈 PDF 페이지 템플릿이 원본 노트에 없습니다.",
                )
            alignment = _choose_alignment(source_document, target_document, match)

            existing_uuids = {entry.uuid for entry in order.entries}
            make_uuid = uuid_factory or (lambda: str(uuid_module.uuid4()))
            make_hash = hash_factory or secrets.token_bytes
            page_root = PurePosixPath(order_name).parent
            slots: list[_PdfSlot] = []
            additions: dict[str, ArchiveAddition] = {}
            deletions: set[str] = set()

            for pair in match.pairs:
                output_index = len(slots)
                if pair.matched:
                    source_page = source_pages[pair.source_index]
                    blob = source_page.blob
                    if pair.target_index is not None:
                        transform = canvas_transform(
                            source_document[pair.source_index],
                            target_document[pair.target_index],
                            (source_page.info.canvas_width, source_page.info.canvas_height),
                            alignment,
                        )
                        blob = transform_page_ink(blob, transform)
                        canvas = (
                            max(1, round(transform.target_width)),
                            max(1, round(transform.target_height)),
                        )
                        blob = patch_page(
                            blob,
                            pdf_page_index=output_index,
                            canvas=canvas,
                            pdf_rect=(0, 0, *canvas),
                        )
                    else:
                        blob = patch_page(blob, pdf_page_index=output_index)
                    slots.append(
                        _PdfSlot(
                            source_page.name,
                            blob,
                            pair.source_index,
                            pair.target_index,
                            pair.source_index,
                            False,
                        )
                    )
                elif pair.source_index is not None:
                    source_page = source_pages[pair.source_index]
                    if is_blank_page(source_page.blob):
                        deletions.add(source_page.name)
                        continue
                    blob = patch_page(source_page.blob, pdf_page_index=output_index)
                    slots.append(
                        _PdfSlot(
                            source_page.name,
                            blob,
                            pair.source_index,
                            None,
                            pair.source_index,
                            False,
                        )
                    )
                else:
                    assert pair.target_index is not None and blank_template is not None
                    new_uuid = _new_uuid(existing_uuids, make_uuid)
                    new_hash = make_hash(32)
                    _require(len(new_hash) == 32, "새 페이지 해시 생성기가 32바이트를 돌려주지 않았습니다.")
                    blob = patch_page(
                        blank_template.blob,
                        pdf_page_index=output_index,
                        uuid=new_uuid,
                        new_page_hash=new_hash,
                    )
                    if pair.target_index is not None:
                        transform = canvas_transform(
                            source_document[blank_template.info.pdf.page_index],
                            target_document[pair.target_index],
                            (
                                blank_template.info.canvas_width,
                                blank_template.info.canvas_height,
                            ),
                            alignment,
                        )
                        canvas = (
                            max(1, round(transform.target_width)),
                            max(1, round(transform.target_height)),
                        )
                        blob = patch_page(
                            blob,
                            canvas=canvas,
                            pdf_rect=(0, 0, *canvas),
                        )
                    name = _page_name(page_root, new_uuid)
                    additions[name] = ArchiveAddition(blank_template.name, blob)
                    slots.append(
                        _PdfSlot(
                            name,
                            blob,
                            None,
                            pair.target_index,
                            blank_template.info.pdf.page_index,
                            True,
                        )
                    )

            if all(
                slot.target_index == output_index
                for output_index, slot in enumerate(slots)
            ):
                rebuilt_pdf_bytes = target.read_bytes()
            else:
                with pymupdf.open() as rebuilt_pdf:
                    for slot in slots:
                        if slot.target_index is None:
                            rebuilt_pdf.insert_pdf(
                                source_document,
                                from_page=slot.source_index,
                                to_page=slot.source_index,
                            )
                            continue
                        rebuilt_pdf.insert_pdf(
                            target_document,
                            from_page=slot.target_index,
                            to_page=slot.target_index,
                        )
                    rebuilt_pdf_bytes = rebuilt_pdf.tobytes(garbage=4, deflate=True)
        finally:
            source_document.close()
            target_document.close()

        ordered_pages = [(slot.page_name, slot.page_blob) for slot in slots]
        ordered_pages.extend((page.name, page.blob) for page in supplemental)
        rebuilt_order = PageOrder(
            file_hash=order.file_hash,
            entries=tuple(
                PageOrderEntry(read_page(blob).uuid, read_page(blob).page_hash)
                for _name, blob in ordered_pages
            ),
        )
        page_heights = [read_page(blob).canvas_height for _name, blob in ordered_pages]
        patched_note = patch_note_height(note_blob, page_heights)
        pdf_hash = hashlib.sha256(rebuilt_pdf_bytes).hexdigest()
        patched_media_info = bytearray(media_info)
        patched_media_info[pdf_entry.hash_offset:pdf_entry.hash_offset + 64] = pdf_hash.encode("ascii")

        replacements = {
            embedded_name: rebuilt_pdf_bytes,
            media_info_name: bytes(patched_media_info),
            order_name: rebuilt_order.to_bytes(),
            note_name: patched_note,
        }
        for slot in slots:
            if not slot.added:
                original = archive.read(slot.page_name)
                if slot.page_blob != original:
                    replacements[slot.page_name] = slot.page_blob
    finally:
        archive.close()

    handle, temporary_name = tempfile.mkstemp(
        dir=output.parent, prefix=f".{output.stem}-", suffix=".tmp.sdocx"
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        trailer = _rewrite_archive(
            source,
            temporary,
            replacements,
            additions=additions,
            deletions=deletions,
        )
        if _read_trailer(temporary) != trailer:
            raise SdocxRebuildError("저장된 SDOCX의 Samsung 꼬리표 검증에 실패했습니다.")
        with ZipFile(temporary) as check:
            checked_members = _safe_members(check)
            expected_names = (set(members) - deletions) | set(additions)
            _require(set(checked_members) == expected_names, "재조립한 SDOCX의 엔트리 구성이 계획과 다릅니다.")
            _require(hashlib.sha256(check.read(embedded_name)).hexdigest() == pdf_hash, "재조립한 PDF 해시가 다릅니다.")
            checked_media = next(
                item for item in parse_media_info(check.read(media_info_name))
                if item.filename == pdf_entry.filename
            )
            _require(checked_media.file_hash == pdf_hash, "mediaInfo.dat의 PDF 해시가 다릅니다.")
            checked_order = read_page_order(check.read(order_name))
            _require(checked_order == rebuilt_order, "재조립한 페이지 순서가 계획과 다릅니다.")
            _require(
                read_note(check.read(note_name)).height == read_note(patched_note).height,
                "재조립한 노트 높이가 계획과 다릅니다.",
            )
            for entry in checked_order.entries:
                blob = check.read(_page_name(PurePosixPath(order_name).parent, entry.uuid))
                _require(read_page(blob).page_hash == entry.page_hash, f"재조립한 페이지 해시가 다릅니다: {entry.uuid}")
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return {
        "path": str(output),
        "size": output.stat().st_size,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "mode": mode,
        "page_count": len(slots),
        "note_page_count": len(ordered_pages),
        "matched_count": len(match.matched_pairs),
        "inserted_target_count": len(match.target_only),
        "preserved_source_only_count": sum(
            1 for slot in slots if slot.target_index is None
        ),
        "dropped_blank_count": len(deletions),
        "footer_size": len(trailer),
        "alignment": alignment.as_dict() if alignment else None,
    }
