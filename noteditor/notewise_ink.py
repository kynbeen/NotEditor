"""Notewise 페이지 메시지에서 펜·형광펜 객체를 읽어 미리보기 레이어로 그린다.

저장에는 쓰지 않는다. 여기서 해석한 값으로 페이지를 다시 쓰지 않고, 사용자가 저장 전에
"필기가 새 배경 위 제자리에 오는지" 눈으로 확인할 투명 PNG만 만든다. 그래서 아직 뜻을 모르는
객체는 오류를 내지 않고 그냥 건너뛴다 — 미리보기에 안 보일 뿐 저장 결과에는 그대로 남는다.
"""
from __future__ import annotations

import base64
import struct
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageColor, ImageDraw

from .ink_transform import CanvasTransform
from .notewise_proto import field_values as _message_values


@dataclass(frozen=True)
class NotewiseStroke:
    kind: str
    points: tuple[tuple[float, float], ...]
    widths: tuple[float, ...]
    color: str
    opacity: float


def _floats(payload: bytes | int | None) -> tuple[float, ...]:
    if not isinstance(payload, bytes) or len(payload) % 4:
        return ()
    return struct.unpack(f"<{len(payload) // 4}f", payload)


def _fixed_float(payload: bytes | int | None, default: float) -> float:
    values = _floats(payload)
    return values[0] if values else default


def _style(payload: bytes | int | None) -> tuple[str, float]:
    if not isinstance(payload, bytes):
        return "#000000", 1.0
    fields = _message_values(payload)
    color_value = fields.get(1, [b"#000000"])[0]
    try:
        color = bytes(color_value).decode("ascii")
        ImageColor.getrgb(color)
    except (ValueError, UnicodeDecodeError):
        color = "#000000"
    opacity = _fixed_float(fields.get(2, [None])[0], 1.0)
    return color, max(0.0, min(1.0, opacity))


def _transform(payload: bytes | int | None, x: float, y: float) -> tuple[float, float]:
    if not isinstance(payload, bytes):
        return x, y
    fields = _message_values(payload)
    values = {
        index: _fixed_float(fields.get(index, [None])[0], 1.0 if index in (1, 5, 9) else 0.0)
        for index in range(1, 10)
    }
    return (
        values[1] * x + values[2] * y + values[3],
        values[4] * x + values[5] * y + values[6],
    )


def _canvas_size(page_fields: dict[int, list[bytes | int]]) -> tuple[float, float] | None:
    settings = page_fields.get(6, [None])[0]
    if not isinstance(settings, bytes):
        return None
    outer = _message_values(settings)
    dimensions = outer.get(1, [None])[0]
    if not isinstance(dimensions, bytes):
        return None
    values = _message_values(dimensions)
    width = values.get(3, [0])[0]
    height = values.get(4, [0])[0]
    if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
        return float(width), float(height)
    return None


def read_notewise_strokes(page_payload: bytes) -> tuple[tuple[NotewiseStroke, ...], tuple[float, float]]:
    """지원하는 필기 객체와 그 페이지의 캔버스 크기를 돌려준다."""
    message = base64.b64decode(page_payload, validate=False)
    page_fields = _message_values(message)
    strokes: list[NotewiseStroke] = []
    for object_payload in page_fields.get(4, []):
        if not isinstance(object_payload, bytes):
            continue
        outer = _message_values(object_payload)
        transform = outer.get(3, [None])[0]
        if 4 in outer:  # 굵기가 변하는 펜
            pen = _message_values(bytes(outer[4][0]))
            xs, ys, widths = _floats(pen.get(4, [None])[0]), _floats(pen.get(5, [None])[0]), _floats(pen.get(6, [None])[0])
            color, opacity = _style(pen.get(3, [None])[0])
            kind = "pen"
        elif 5 in outer:  # 굵기가 일정한 형광펜
            pen = _message_values(bytes(outer[5][0]))
            xs, ys = _floats(pen.get(3, [None])[0]), _floats(pen.get(4, [None])[0])
            width = float(pen.get(1, [1])[0]) if isinstance(pen.get(1, [1])[0], int) else 1.0
            widths = (width,) * min(len(xs), len(ys))
            color, opacity = _style(pen.get(2, [None])[0])
            kind = "highlighter"
        else:
            continue
        count = min(len(xs), len(ys))
        if count < 1:
            continue
        points = tuple(_transform(transform, xs[index], ys[index]) for index in range(count))
        if len(widths) < count:
            widths = widths + ((widths[-1] if widths else 1.0),) * (count - len(widths))
        strokes.append(NotewiseStroke(kind, points, tuple(widths[:count]), color, opacity))

    canvas = _canvas_size(page_fields)
    if canvas is None:
        max_x = max((x for stroke in strokes for x, _y in stroke.points), default=1.0)
        max_y = max((y for stroke in strokes for _x, y in stroke.points), default=1.0)
        canvas = max(1.0, max_x), max(1.0, max_y)
    return tuple(strokes), canvas


def render_notewise_ink(
    page_payload: bytes,
    size: tuple[int, int],
    transform: CanvasTransform | None = None,
) -> tuple[bytes, int]:
    strokes, (canvas_width, canvas_height) = read_notewise_strokes(page_payload)
    if transform is not None:
        canvas_width, canvas_height = transform.target_width, transform.target_height
    width, height = size
    scale_x, scale_y = width / canvas_width, height / canvas_height
    width_scale = (scale_x + scale_y) / 2
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    for stroke in strokes:
        rgb = ImageColor.getrgb(stroke.color)
        alpha = round(255 * stroke.opacity)
        points = [
            (mapped_x * scale_x, mapped_y * scale_y)
            for x, y in stroke.points
            for mapped_x, mapped_y in [
                transform.point(x, y) if transform is not None else (x, y)
            ]
        ]
        stroke_scale = transform.width_scale if transform else 1.0
        if len(points) == 1:
            radius = max(0.5, stroke.widths[0] * stroke_scale * width_scale / 2)
            x, y = points[0]
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*rgb, alpha))
            continue
        for index in range(1, len(points)):
            segment_width = max(1, round(
                (stroke.widths[index - 1] + stroke.widths[index])
                * stroke_scale * width_scale / 2
            ))
            draw.line((points[index - 1], points[index]), fill=(*rgb, alpha), width=segment_width)
    output = BytesIO()
    layer.save(output, format="PNG")
    return output.getvalue(), len(strokes)
