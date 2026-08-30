"""필기 이전에 공통으로 쓰는 판단: 오류 부모, 진단 결과, 원본·대상 PDF 짝 맞추기.

Samsung Notes(SDOCX)든 Notewise든 "문서에 들어 있는 PDF를 새 PDF로 갈아 끼운다"는 절차는
같다. 그대로 넣을지(``exact``), 본문 기준으로 다시 앉힐지(``aligned``), 쪽 구성을 다시 짜야
하는지(``rebuild``)를 정하는 판단은 필기 형식과 무관하므로 여기 한곳에 둔다.

오류 메시지에는 형식별 이름이 그대로 드러나야 하므로, 어느 예외를 던질지와 PDF를 뭐라고
부를지는 호출하는 쪽이 ``error``·``source_label``로 넘긴다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from collections.abc import Callable, Sequence

from .alignment import Alignment, estimate_alignment, place_page
from .page_match import MatchResult, match_pages


class HandwritingTransferError(RuntimeError):
    """필기 이전 중 사용자에게 그대로 보여줄 수 있는 오류의 공통 부모."""


@dataclass(frozen=True)
class TransferInspection:
    source_name: str
    target_name: str
    page_count: int
    annotated_page_count: int
    stroke_cache_count: int
    embedded_pdf_name: str
    target_size: int
    source_page_count: int | None = None
    mode: str = "exact"
    alignment: Alignment | None = None
    match: MatchResult | None = None

    def as_dict(self) -> dict:
        return {
            "source_name": self.source_name,
            "target_name": self.target_name,
            "page_count": self.page_count,
            "annotated_page_count": self.annotated_page_count,
            "stroke_cache_count": self.stroke_cache_count,
            "embedded_pdf_name": self.embedded_pdf_name,
            "target_size": self.target_size,
            "source_page_count": self.source_page_count,
            "mode": self.mode,
            "alignment": self.alignment.as_dict() if self.alignment else None,
            "match": self.match.as_dict() if self.match else None,
        }


def open_pdf(
    pdf: bytes | Path, label: str, *, error: type[Exception] = HandwritingTransferError
):
    """PDF를 열고 필기 이전에 쓸 수 없는 문서는 그 자리에서 거른다. 호출자가 닫는다."""
    import pymupdf

    try:
        if isinstance(pdf, Path):
            document = pymupdf.open(pdf)
        else:
            document = pymupdf.open(stream=pdf, filetype="pdf")
    except Exception as exc:
        raise error(f"{label}를 읽을 수 없습니다: {exc}") from exc
    try:
        if document.needs_pass:
            raise error(f"암호화된 {label}는 지원하지 않습니다.")
        if document.page_count < 1:
            raise error(f"페이지가 없는 {label}입니다.")
    except Exception:
        document.close()
        raise
    return document


def geometry(document) -> list[tuple[float, float, int]]:
    return [
        (round(float(page.rect.width), 3), round(float(page.rect.height), 3), int(page.rotation))
        for page in document
    ]


def geometry_mismatches(
    source: list[tuple[float, float, int]], target: list[tuple[float, float, int]]
) -> list[int]:
    """크기나 회전이 다른 쪽의 번호(1부터)를 모은다."""
    mismatches = []
    for index, (left, right) in enumerate(zip(source, target), start=1):
        same_size = abs(left[0] - right[0]) <= 0.5 and abs(left[1] - right[1]) <= 0.5
        if not same_size or left[2] != right[2]:
            mismatches.append(index)
    return mismatches


def build_background_pdf(
    source_document,
    target_document,
    mapping: Sequence[int | None],
    alignment: Alignment,
    *,
    reference_index: int = 0,
    error: type[Exception] = HandwritingTransferError,
) -> bytes:
    """대상 PDF를 원본 페이지 좌표계에 다시 앉힌 배경 PDF 바이트를 만든다.

    ``mapping[대상쪽] = 원본쪽`` 이고, 새로 끼어들어 짝이 없는 쪽은 ``None`` 이다. 그런 쪽에는
    기준 좌표계가 없으므로 ``reference_index`` 원본 쪽의 크기를 빌린다.

    필기 좌표는 절대 건드리지 않고 배경만 옮기는 것이 이 프로젝트의 전제다. 그래서 결과
    PDF의 모든 쪽은 원본 캔버스와 같은 크기여야 하고, 아니면 내보내지 않고 거절한다.
    """
    import pymupdf

    source_geometry = geometry(source_document)
    expected = [
        source_geometry[reference_index if index is None else index] for index in mapping
    ]
    with pymupdf.open() as document:
        for target_index, source_index in enumerate(mapping):
            reference = source_document[
                reference_index if source_index is None else source_index
            ]
            place_page(document, reference, target_document, target_index, alignment)
        built = geometry(document)
        payload = document.tobytes(garbage=4, deflate=True)

    mismatches = geometry_mismatches(expected, built)
    if mismatches:
        shown = ", ".join(map(str, mismatches[:8]))
        suffix = "…" if len(mismatches) > 8 else ""
        raise error(
            f"정렬한 배경의 {shown}{suffix}쪽 크기가 원본 캔버스와 달라 저장하지 않았습니다."
        )
    return payload


def plan_transfer(
    embedded_pdf: bytes,
    target: Path,
    *,
    source_label: str = "내장 PDF",
    error: type[Exception] = HandwritingTransferError,
    progress: Callable[[str], None] | None = None,
) -> tuple[str, Alignment | None, int, MatchResult]:
    """그대로 넣을지(``exact``), 본문 기준으로 다시 앉힐지(``aligned``) 정한다."""
    source_document = open_pdf(embedded_pdf, source_label, error=error)
    try:
        target_document = open_pdf(target, "대상 PDF", error=error)
    except Exception:
        source_document.close()
        raise
    try:
        source_geometry = geometry(source_document)
        target_geometry = geometry(target_document)
        if progress:
            progress("matching")
        match = match_pages(source_document, target_document)
        matched_indices = [
            (pair.source_index, pair.target_index) for pair in match.matched_pairs
        ]
        same_geometry = all(
            abs(source_geometry[source][0] - target_geometry[target_index][0]) <= 0.5
            and abs(source_geometry[source][1] - target_geometry[target_index][1]) <= 0.5
            and source_geometry[source][2] == target_geometry[target_index][2]
            for source, target_index in matched_indices
        )
        if progress:
            progress("alignment")
        alignment = estimate_alignment(source_document, target_document, matched_indices)
        if progress:
            progress("preview")
    finally:
        source_document.close()
        target_document.close()

    if alignment is None:
        if not same_geometry:
            raise error(
                "페이지 크기가 다른데 두 문서의 본문 영역을 찾지 못해 정렬 배율을 정할 수 없습니다. "
                "내용이 비어 있거나 스캔 품질이 낮은 문서일 수 있습니다."
            )
        mode = "rebuild" if match.source_only or match.target_only else "exact"
        return mode, None, len(target_geometry), match
    if same_geometry and not (alignment.improves and alignment.axes_agree):
        # 페이지 크기가 같고 본문 배치도 그대로면 사용자의 PDF를 바이트 그대로 넣는다.
        mode = "rebuild" if match.source_only or match.target_only else "exact"
        return mode, None, len(target_geometry), match
    mode = "rebuild" if match.source_only or match.target_only else "aligned"
    return mode, alignment, len(target_geometry), match
