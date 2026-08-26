"""새 배경 PDF를 원본 페이지 좌표계에 '본문 기준'으로 다시 앉힌다.

Samsung Notes의 필기는 ``.page`` 안의 캔버스 좌표에 저장되고, 배경 PDF는 그 캔버스의
사각형에 그려지는 페이지 속성이다. 즉 필기와 배경은 별개 좌표계로 얹혀 있으므로, 필기
좌표를 재직렬화하지 않고 **배경 쪽을 옮겨** 본문과 필기의 상대 위치를 맞출 수 있다.

여기서 하는 일은 새 PDF를 원본 페이지 크기의 캔버스에 확대/축소·이동해서 다시 그리는
것이다. 확대/축소·여백 증가·둘의 조합은 모두 하나의 상사변환 ``x' = s·x + t`` 이라
케이스를 나누지 않는다. 배율은 문서 전체에 대해 하나만 쓰고, 여러 쪽의 본문 상자에서
중앙값으로 추정한다 (쪽마다 따로 맞추면 내용량 차이 때문에 쪽마다 어긋난다).

본문 상자는 페이지를 회색으로 렌더링해 잉크가 있는 영역으로 찾는다. 벡터 객체를 세는
방법보다 스캔 PDF·배경색이 있는 문서까지 같은 코드로 다룰 수 있다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import median

_INK_MAX_SIDE = 1200
_INK_TOLERANCE = 6
_SAMPLE_LIMIT = 12
_MIN_SAMPLES = 2
_IDENTITY_SCALE = 0.002
_IDENTITY_SHIFT = 1.0
_AXIS_TOLERANCE = 0.005
_MEANINGFUL_RESIDUAL = 2.0
_POINTS_PER_MM = 72.0 / 25.4


def points_to_mm(points: float) -> float:
    return points / _POINTS_PER_MM


@dataclass(frozen=True)
class Alignment:
    """새 PDF를 원본 페이지 좌표계로 옮기는 변환과 그 품질 지표."""

    scale: float
    offset_x: float
    offset_y: float
    sampled_pages: int
    residual: float
    identity_residual: float
    aspect_scale: float
    aspect_drift: float
    clipped: float

    @property
    def identity(self) -> bool:
        return (
            abs(self.scale - 1.0) <= _IDENTITY_SCALE
            and abs(self.offset_x) <= _IDENTITY_SHIFT
            and abs(self.offset_y) <= _IDENTITY_SHIFT
        )

    @property
    def axes_agree(self) -> bool:
        """가로·세로 배율이 같아야 상사변환이다. 다르면 본문 배치가 바뀐 게 아니다."""
        return abs(self.aspect_scale - self.scale) <= _AXIS_TOLERANCE * self.scale

    @property
    def improves(self) -> bool:
        """변환이 '그대로 두기'보다 본문을 실제로 더 잘 맞추는지."""
        return (
            self.identity_residual > _MEANINGFUL_RESIDUAL
            and self.residual <= 0.5 * self.identity_residual
        )

    def place(self, rect) -> tuple[float, float, float, float]:
        """새 PDF의 사각형을 원본 페이지 좌표계로 옮긴다."""
        return (
            self.offset_x + self.scale * rect.x0,
            self.offset_y + self.scale * rect.y0,
            self.offset_x + self.scale * rect.x1,
            self.offset_y + self.scale * rect.y1,
        )

    def as_dict(self) -> dict:
        return {
            "scale": round(self.scale, 5),
            "offset_x_mm": round(points_to_mm(self.offset_x), 2),
            "offset_y_mm": round(points_to_mm(self.offset_y), 2),
            "sampled_pages": self.sampled_pages,
            "residual_mm": round(points_to_mm(self.residual), 2),
            "identity_residual_mm": round(points_to_mm(self.identity_residual), 2),
            "aspect_scale": round(self.aspect_scale, 5),
            "aspect_drift_mm": round(points_to_mm(self.aspect_drift), 2),
            "clipped_mm": round(points_to_mm(self.clipped), 2),
            "identity": self.identity,
            "axes_agree": self.axes_agree,
            "improves": self.improves,
        }


def _sample_indices(page_count: int, limit: int) -> list[int]:
    if page_count <= limit:
        return list(range(page_count))
    step = (page_count - 1) / (limit - 1)
    return sorted({round(index * step) for index in range(limit)})


def ink_box(page, max_side: int = _INK_MAX_SIDE):
    """페이지에서 잉크가 있는 영역을 PDF 좌표로 돌려준다. 빈 쪽이면 ``None``."""
    import pymupdf

    rect = page.rect
    if rect.is_empty or rect.is_infinite:
        return None
    scale = min(max_side / max(rect.width, rect.height), 4.0)
    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY, alpha=False
    )
    width, height, samples = pixmap.width, pixmap.height, pixmap.samples
    if width < 2 or height < 2:
        return None
    corners = sorted(
        (samples[0], samples[width - 1], samples[(height - 1) * width], samples[height * width - 1])
    )
    background = corners[1]
    table = bytes(1 if abs(value - background) > _INK_TOLERANCE else 0 for value in range(256))

    top = bottom = None
    left, right = width, -1
    for row_index in range(height):
        row = samples[row_index * width:(row_index + 1) * width].translate(table)
        first = row.find(b"\x01")
        if first < 0:
            continue
        if top is None:
            top = row_index
        bottom = row_index
        left = min(left, first)
        right = max(right, row.rfind(b"\x01"))
    if top is None or right < left:
        return None
    return pymupdf.Rect(
        (pixmap.x + left) / scale,
        (pixmap.y + top) / scale,
        (pixmap.x + right + 1) / scale,
        (pixmap.y + bottom + 1) / scale,
    )


def estimate_alignment(
    source_document,
    target_document,
    page_pairs: list[tuple[int, int]] | tuple[tuple[int, int], ...] | None = None,
) -> Alignment | None:
    """두 문서의 본문 상자에서 문서 전체에 쓸 상사변환 하나를 추정한다."""
    if page_pairs is None:
        page_count = min(source_document.page_count, target_document.page_count)
        candidates = [(index, index) for index in range(page_count)]
    else:
        candidates = list(page_pairs)
    sampled = [candidates[index] for index in _sample_indices(len(candidates), _SAMPLE_LIMIT)]
    boxes = []
    scales_x: list[float] = []
    scales_y: list[float] = []
    for source_index, target_index in sampled:
        old_box = ink_box(source_document[source_index])
        new_box = ink_box(target_document[target_index])
        if old_box is None or new_box is None:
            continue
        if new_box.width < 1 or new_box.height < 1 or old_box.width < 1 or old_box.height < 1:
            continue
        boxes.append((source_index, old_box, new_box))
        scales_x.append(old_box.width / new_box.width)
        scales_y.append(old_box.height / new_box.height)
    if len(boxes) < _MIN_SAMPLES:
        return None

    scale = median(scales_x)
    aspect_scale = median(scales_y)
    offset_x = median(old.x0 - scale * new.x0 for _index, old, new in boxes)
    offset_y = median(old.y0 - scale * new.y0 for _index, old, new in boxes)

    residual = 0.0
    identity_residual = 0.0
    aspect_drift = 0.0
    clipped = 0.0
    for index, old, new in boxes:
        left, top, right, bottom = (
            offset_x + scale * new.x0,
            offset_y + scale * new.y0,
            offset_x + scale * new.x1,
            offset_y + scale * new.y1,
        )
        residual = max(
            residual,
            abs(left - old.x0), abs(top - old.y0),
            abs(right - old.x1), abs(bottom - old.y1),
        )
        identity_residual = max(
            identity_residual,
            abs(new.x0 - old.x0), abs(new.y0 - old.y0),
            abs(new.x1 - old.x1), abs(new.y1 - old.y1),
        )
        aspect_drift = max(aspect_drift, abs(aspect_scale - scale) * new.height)
        page_rect = source_document[index].rect
        clipped = max(
            clipped,
            page_rect.x0 - left, page_rect.y0 - top,
            right - page_rect.x1, bottom - page_rect.y1,
            0.0,
        )
    return Alignment(
        scale=scale,
        offset_x=offset_x,
        offset_y=offset_y,
        sampled_pages=len(boxes),
        residual=residual,
        identity_residual=identity_residual,
        aspect_scale=aspect_scale,
        aspect_drift=aspect_drift,
        clipped=clipped,
    )


def place_page(output_document, source_page, target_document, page_index: int, alignment: Alignment):
    """원본 페이지 크기의 새 쪽을 만들고 대상 PDF 쪽을 변환해 그린다."""
    import pymupdf

    source_rect = source_page.rect
    page = output_document.new_page(width=source_rect.width, height=source_rect.height)
    destination = pymupdf.Rect(*alignment.place(target_document[page_index].rect))
    page.show_pdf_page(destination, target_document, page_index, keep_proportion=True, clip=None)
    return page


def build_aligned_pdf(
    source_document,
    target_document,
    alignment: Alignment,
    output_path: str | Path,
) -> Path:
    """대상 PDF를 원본 페이지 좌표계에 맞춰 다시 그린 PDF를 만든다."""
    import pymupdf

    output = Path(output_path)
    with pymupdf.open() as document:
        for index in range(source_document.page_count):
            place_page(document, source_document[index], target_document, index, alignment)
        document.save(output, garbage=4, deflate=True)
    return output


def render_comparison(
    source_document,
    target_document,
    alignment: Alignment | None,
    page_index: int,
    max_side: int = 900,
    target_page_index: int | None = None,
) -> tuple[bytes, bytes]:
    """같은 크기로 렌더링한 (원본 배경, 정렬된 새 배경) PNG 쌍을 돌려준다."""
    import pymupdf

    source_page = source_document[page_index]
    target_index = page_index if target_page_index is None else target_page_index
    rect = source_page.rect
    scale = min(max_side / max(rect.width, rect.height), 3.0)
    matrix = pymupdf.Matrix(scale, scale)
    before = source_page.get_pixmap(matrix=matrix, alpha=False).tobytes("png")
    with pymupdf.open() as staged:
        if alignment is None:
            staged.insert_pdf(target_document, from_page=target_index, to_page=target_index)
        else:
            place_page(staged, source_page, target_document, target_index, alignment)
        after = staged[0].get_pixmap(matrix=matrix, alpha=False).tobytes("png")
    return before, after
