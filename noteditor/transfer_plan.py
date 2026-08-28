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

from .alignment import Alignment, estimate_alignment
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


def plan_transfer(
    embedded_pdf: bytes,
    target: Path,
    *,
    source_label: str = "내장 PDF",
    error: type[Exception] = HandwritingTransferError,
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
        alignment = estimate_alignment(source_document, target_document, matched_indices)
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
