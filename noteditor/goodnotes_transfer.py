"""Goodnotes 6 필기를 새 PDF 배경 위로 옮긴다.

``.goodnotes`` 는 ZIP이고, 필기는 ``notes/<쪽 내용 ID>`` 저널에, 배경은
``attachments/<첨부 ID>`` PDF에 들어 있다. 둘을 이어 주는 것은 ``index.events.pb`` 의
용지·쪽 생성·쪽 연결 기록이다(자세한 구조는 ``goodnotes_archive``).

**필기 저널은 바이트 하나 건드리지 않고 옮긴다.** 획의 좌표는 쪽 캔버스 기준이라 캔버스
크기만 그대로 두면 필기는 제자리에 남는다. 그래서 다시 쓰는 것은 배경 첨부와 그것을
가리키는 참조, 그리고 쪽 순서뿐이다. 획을 다시 인코딩하면 앱이 알아보지 못하는 모양으로
번지는 것이 알려져 있어, 이 경계를 넘지 않는 것이 이 형식 지원의 핵심이다.

쪽을 더하거나 지우거나 순서를 바꾸면 쪽 ID가 새로 필요하므로 아카이브를 다시 만든다.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from PIL import Image

from .alignment import Alignment, render_comparison
from .goodnotes_archive import (
    GoodnotesDocument,
    GoodnotesPage,
    background_pdf,
    build_events,
    build_index,
    new_page_ids,
    read_document,
    safe_members,
)
from .goodnotes_ink import count_goodnotes_strokes, render_goodnotes_ink
from .goodnotes_outline import (
    PAGE_BASIS_SOURCE,
    PAGE_BASIS_TARGET,
    append_outline_events,
    load_outline,
    map_outline_to_result,
    validate_outline,
    verify_outline_events,
)
from .goodnotes_proto import GoodnotesTransferError
from .page_match import MatchResult
from .page_plan import PagePlan
from .transfer_plan import (
    TransferInspection,
    build_planned_background_pdf,
    open_pdf,
    plan_transfer,
)

_SOURCE_LABEL = "Goodnotes 배경 PDF"


def _open_archive(source: Path) -> ZipFile:
    try:
        return ZipFile(source, "r")
    except BadZipFile as exc:
        raise GoodnotesTransferError(
            f"Goodnotes 파일을 열 수 없습니다: {source.name}"
        ) from exc


def _checked_paths(source: str | Path, target: str | Path) -> tuple[Path, Path]:
    source_path = Path(source).expanduser().resolve()
    target_path = Path(target).expanduser().resolve()
    if source_path.suffix.lower() != ".goodnotes" or not source_path.is_file():
        raise GoodnotesTransferError("필기가 들어 있는 .goodnotes 파일을 선택하세요.")
    if target_path.suffix.lower() != ".pdf" or not target_path.is_file():
        raise GoodnotesTransferError("새 배경으로 사용할 PDF를 선택하세요.")
    return source_path, target_path


def _stroke_counts(archive: ZipFile, document: GoodnotesDocument) -> list[int]:
    counts = []
    for page in document.pages:
        if page.notes_member is None:
            counts.append(0)
            continue
        counts.append(count_goodnotes_strokes(archive.read(page.notes_member)))
    return counts


def inspect_goodnotes_transfer(
    source_goodnotes: str | Path,
    target_pdf: str | Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> TransferInspection:
    source, target = _checked_paths(source_goodnotes, target_pdf)
    if progress:
        progress("structure")
    with _open_archive(source) as archive:
        members = safe_members(archive)
        document = read_document(archive, members)
        embedded_pdf = background_pdf(archive, document)
        stroke_counts = _stroke_counts(archive, document)
    mode, alignment, page_count, match = plan_transfer(
        embedded_pdf,
        target,
        source_label=_SOURCE_LABEL,
        error=GoodnotesTransferError,
        progress=progress,
    )
    return TransferInspection(
        source_name=source.name,
        target_name=target.name,
        page_count=page_count,
        annotated_page_count=sum(count > 0 for count in stroke_counts),
        stroke_cache_count=sum(stroke_counts),
        embedded_pdf_name=f"{len(document.attachments)}개 첨부 배경",
        target_size=target.stat().st_size,
        source_page_count=len(document.pages),
        mode=mode,
        alignment=alignment,
        match=match,
    )


def _planned_background_bytes(
    embedded_pdf: bytes, target: Path, plan: PagePlan, alignment: Alignment | None
) -> bytes:
    """결과 쪽 순서대로 배경 PDF를 만든다.

    Goodnotes 첨부는 쪽마다 "이 첨부의 몇 쪽"을 가리킨다. 결과에서는 첨부 하나에 쪽을
    결과 순서 그대로 담아, 쪽 번호가 곧 결과 쪽 번호가 되게 한다.
    """
    source_document = open_pdf(embedded_pdf, _SOURCE_LABEL, error=GoodnotesTransferError)
    try:
        target_document = open_pdf(target, "대상 PDF", error=GoodnotesTransferError)
    except Exception:
        source_document.close()
        raise
    with source_document, target_document:
        return build_planned_background_pdf(
            source_document,
            target_document,
            plan,
            alignment,
            error=GoodnotesTransferError,
        )


def _thumbnail(background: bytes, notes: bytes | None, canvas: tuple[float, float]) -> bytes:
    """서재에 보이는 미리보기. 새 배경 첫 쪽 위에 그 쪽 필기를 얹는다."""
    import pymupdf

    with pymupdf.open(stream=background, filetype="pdf") as document:
        page = document[0]
        scale = min(386 / max(page.rect.width, 1.0), 3.0)
        payload = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
    with Image.open(BytesIO(payload)) as rendered:
        sheet = rendered.convert("RGB")
        if notes:
            ink, _count = render_goodnotes_ink(notes, sheet.size, canvas)
            with Image.open(BytesIO(ink)) as layer:
                sheet.paste(layer, (0, 0), layer)
        output = BytesIO()
        sheet.save(output, format="JPEG", quality=82)
    return output.getvalue()


def _copy_member(archive: ZipFile, members: dict, name: str, default: bytes) -> bytes:
    return archive.read(name) if name in members else default


def transfer_goodnotes_handwriting(
    source_goodnotes: str | Path,
    target_pdf: str | Path,
    output_goodnotes: str | Path,
    *,
    match_override: MatchResult | None = None,
    plan_override: PagePlan | None = None,
    outline_path: str | Path | None = None,
    outline_entries: list[dict] | None = None,
    outline_page_basis: str = PAGE_BASIS_TARGET,
) -> dict:
    source, target = _checked_paths(source_goodnotes, target_pdf)
    output = Path(output_goodnotes).expanduser().resolve()
    if output.suffix.lower() != ".goodnotes":
        output = output.with_name(output.name + ".goodnotes")
    if output == source:
        raise GoodnotesTransferError("원본 Goodnotes 파일을 덮어쓸 수 없습니다.")

    inspection = inspect_goodnotes_transfer(source, target)
    match = match_override or inspection.match
    plan = plan_override or PagePlan.from_match(
        match, inspection.source_page_count, inspection.page_count
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=output.parent, prefix=f".{output.stem}-", suffix=".tmp.goodnotes"
    )
    os.close(fd)
    temporary = Path(temp_name)
    mapped_outline = ()
    try:
        with _open_archive(source) as archive:
            members = safe_members(archive)
            document = read_document(archive, members)
            embedded_pdf = background_pdf(archive, document)
            attachment = _planned_background_bytes(
                embedded_pdf, target, plan, inspection.alignment
            )

            reference = next(
                (
                    document.pages[slot.source_index]
                    for slot in plan.slots
                    if slot.source_index is not None
                ),
                document.pages[0],
            )
            slots: list[tuple[GoodnotesPage, str, str]] = []
            notes_members: list[tuple[str, bytes]] = []
            index_pairs: list[tuple[str, str]] = []
            for slot in plan.slots:
                page = (
                    reference
                    if slot.source_index is None
                    else document.pages[slot.source_index]
                )
                entity_id, content_id = new_page_ids()
                slots.append((page, entity_id, content_id))
                member = f"notes/{content_id}"
                # 새로 끼어든 쪽은 필기가 없다. 빈 저널도 앱이 받아들이는 형태다.
                payload = b""
                if slot.source_index is not None and page.notes_member:
                    payload = archive.read(page.notes_member)
                notes_members.append((member, payload))
                index_pairs.append((content_id, member))

            attachment_id = str(uuid.uuid4()).upper()
            events = build_events(
                document,
                slots,
                attachment_id,
                len(attachment),
                target.stem,
            )
            if outline_path is not None and outline_entries is not None:
                raise GoodnotesTransferError(
                    "Provide outline_path or outline_entries, not both."
                )
            if outline_path is not None or outline_entries is not None:
                input_page_count = (
                    inspection.source_page_count
                    if outline_page_basis == PAGE_BASIS_SOURCE
                    else inspection.page_count
                )
                outline = (
                    load_outline(outline_path, input_page_count)
                    if outline_path is not None
                    else validate_outline(outline_entries, input_page_count)
                )
                mapped_outline = map_outline_to_result(
                    outline, list(plan.slots), outline_page_basis
                )
                events = append_outline_events(
                    events,
                    [entity_id for _page, entity_id, _content_id in slots],
                    mapped_outline,
                )
            search_blob = b""
            for identifier, member in document.attachments.items():
                candidate = f"search/{identifier}"
                if candidate in members:
                    search_blob = archive.read(candidate)
                    break
            first_notes = notes_members[0][1] if notes_members else b""
            thumbnail = _thumbnail(attachment, first_notes, slots[0][0].canvas)

            with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as result:
                result.writestr("schema.pb", archive.read("schema.pb"))
                result.writestr(
                    "document.info.pb", _copy_member(archive, members, "document.info.pb", b"")
                )
                result.writestr("index.events.pb", events)
                result.writestr("index.notes.pb", build_index(index_pairs))
                result.writestr(
                    "index.attachments.pb",
                    build_index([(attachment_id, f"attachments/{attachment_id}")]),
                )
                result.writestr(
                    "index.search.pb",
                    build_index(
                        [(attachment_id, f"search/{attachment_id}")],
                        {attachment_id: [(3, 0, 1)]},
                    ),
                )
                result.writestr(f"search/{attachment_id}", search_blob)
                result.writestr(f"attachments/{attachment_id}", attachment)
                for member, payload in notes_members:
                    result.writestr(member, payload)
                result.writestr("thumbnail.jpg", thumbnail)

        _validate_output(temporary, plan, attachment, mapped_outline)
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


def _validate_output(
    path: Path,
    plan: PagePlan,
    attachment: bytes,
    outline: tuple | list = (),
) -> None:
    """저장한 파일을 다시 읽어 쪽 수·순서·배경 참조가 계획과 같은지 확인한다."""
    with _open_archive(path) as archive:
        members = safe_members(archive)
        document = read_document(archive, members)
        if outline:
            verify_outline_events(
                archive.read("index.events.pb"),
                [page.entity_id for page in document.pages],
                outline,
            )
        if len(document.pages) != len(plan.slots):
            raise GoodnotesTransferError("저장된 Goodnotes의 페이지 수가 달라졌습니다.")
        if len(document.attachments) != 1:
            raise GoodnotesTransferError("저장된 Goodnotes의 배경 첨부가 하나가 아닙니다.")
        attachment_id, member = next(iter(document.attachments.items()))
        if archive.read(member) != attachment:
            raise GoodnotesTransferError("저장된 Goodnotes의 배경 검증에 실패했습니다.")
        for index, page in enumerate(document.pages):
            if page.attachment_id != attachment_id or page.source_page != index + 1:
                raise GoodnotesTransferError(
                    f"저장된 Goodnotes {index + 1}쪽의 배경 참조가 어긋났습니다."
                )
            if page.notes_member is None:
                raise GoodnotesTransferError(
                    f"저장된 Goodnotes {index + 1}쪽의 필기 항목을 찾을 수 없습니다."
                )


def preview_goodnotes_transfer(
    source_goodnotes: str | Path,
    target_pdf: str | Path,
    page_index: int,
    inspection: TransferInspection | None = None,
    *,
    source_index_override: int = -2,
) -> tuple[bytes, bytes, bytes, int]:
    """이전 배경과 새 배경, 그리고 그 위에 얹을 Goodnotes 필기 레이어를 그린다."""
    import pymupdf

    source, target = _checked_paths(source_goodnotes, target_pdf)
    inspection = inspection or inspect_goodnotes_transfer(source, target)
    target_index = max(0, min(int(page_index), inspection.page_count - 1))
    if source_index_override == -2:
        source_index = next(
            (
                pair.source_index
                for pair in inspection.match.pairs
                if pair.target_index == target_index
            ),
            None,
        )
    elif source_index_override < 0:
        source_index = None
    else:
        source_index = int(source_index_override)
    if source_index is not None and not 0 <= source_index < (inspection.source_page_count or 0):
        source_index = None

    with _open_archive(source) as archive:
        members = safe_members(archive)
        document = read_document(archive, members)
        embedded_pdf = background_pdf(archive, document)
        notes = b""
        canvas = document.pages[0].canvas
        if source_index is not None:
            page = document.pages[source_index]
            canvas = page.canvas
            if page.notes_member:
                notes = archive.read(page.notes_member)

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
        if not notes:
            transparent = Image.new("RGBA", background.size, (0, 0, 0, 0))
            ink_output = BytesIO()
            transparent.save(ink_output, format="PNG")
            ink, stroke_count = ink_output.getvalue(), 0
        else:
            ink, stroke_count = render_goodnotes_ink(notes, background.size, canvas)
    return before, after, ink, stroke_count


__all__ = [
    "GoodnotesTransferError",
    "inspect_goodnotes_transfer",
    "preview_goodnotes_transfer",
    "transfer_goodnotes_handwriting",
]
