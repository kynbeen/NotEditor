"""PDF 본문 정렬을 편집 가능한 필기 캔버스 좌표로 뒤집는다."""
from __future__ import annotations

from dataclasses import dataclass

from .alignment import Alignment


@dataclass(frozen=True)
class CanvasTransform:
    """원본 필기 캔버스 좌표를 대상 PDF 캔버스 좌표로 옮기는 상사변환."""

    scale_x: float
    scale_y: float
    offset_x: float
    offset_y: float
    target_width: float
    target_height: float

    @property
    def identity(self) -> bool:
        return (
            abs(self.scale_x - 1.0) <= 1e-6
            and abs(self.scale_y - 1.0) <= 1e-6
            and abs(self.offset_x) <= 1e-4
            and abs(self.offset_y) <= 1e-4
        )

    @property
    def width_scale(self) -> float:
        return (abs(self.scale_x) + abs(self.scale_y)) / 2.0

    def point(self, x: float, y: float) -> tuple[float, float]:
        return (
            self.scale_x * float(x) + self.offset_x,
            self.scale_y * float(y) + self.offset_y,
        )

    def rect(self, values) -> tuple[float, float, float, float]:
        left, top = self.point(values[0], values[1])
        right, bottom = self.point(values[2], values[3])
        return min(left, right), min(top, bottom), max(left, right), max(top, bottom)


def canvas_transform(
    source_page,
    target_page,
    source_canvas: tuple[float, float],
    alignment: Alignment | None,
) -> CanvasTransform:
    """PDF 점 좌표와 앱 캔버스 좌표의 밀도를 유지하며 정렬의 역변환을 만든다."""
    source_width = max(float(source_page.rect.width), 1e-6)
    source_height = max(float(source_page.rect.height), 1e-6)
    canvas_width = max(float(source_canvas[0]), 1.0)
    canvas_height = max(float(source_canvas[1]), 1.0)
    density_x = canvas_width / source_width
    density_y = canvas_height / source_height
    target_width = float(target_page.rect.width) * density_x
    target_height = float(target_page.rect.height) * density_y

    if alignment is None:
        return CanvasTransform(1.0, 1.0, 0.0, 0.0, target_width, target_height)

    scale = max(float(alignment.scale), 1e-9)
    return CanvasTransform(
        scale_x=1.0 / scale,
        scale_y=1.0 / scale,
        offset_x=-density_x * float(alignment.offset_x) / scale,
        offset_y=-density_y * float(alignment.offset_y) / scale,
        target_width=target_width,
        target_height=target_height,
    )


__all__ = ["CanvasTransform", "canvas_transform"]
