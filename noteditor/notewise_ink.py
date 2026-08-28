"""Read and render pen/highlighter objects from Notewise page messages."""
from __future__ import annotations

import base64
import struct
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageColor, ImageDraw


@dataclass(frozen=True)
class NotewiseStroke:
    kind: str
    points: tuple[tuple[float, float], ...]
    widths: tuple[float, ...]
    color: str
    opacity: float


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise ValueError("invalid protobuf varint")


def _fields(data: bytes):
    offset = 0
    while offset < len(data):
        key, offset = _read_varint(data, offset)
        number, wire_type = key >> 3, key & 7
        if wire_type == 0:
            value, offset = _read_varint(data, offset)
        elif wire_type == 1:
            value, offset = data[offset:offset + 8], offset + 8
        elif wire_type == 2:
            size, offset = _read_varint(data, offset)
            value, offset = data[offset:offset + size], offset + size
        elif wire_type == 5:
            value, offset = data[offset:offset + 4], offset + 4
        else:
            raise ValueError(f"unsupported protobuf wire type: {wire_type}")
        if offset > len(data):
            raise ValueError("truncated protobuf field")
        yield number, wire_type, value


def _message_values(data: bytes) -> dict[int, list[bytes | int]]:
    result: dict[int, list[bytes | int]] = {}
    for number, _wire_type, value in _fields(data):
        result.setdefault(number, []).append(value)
    return result


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
    """Return supported ink objects and the page's canvas dimensions."""
    message = base64.b64decode(page_payload, validate=False)
    page_fields = _message_values(message)
    strokes: list[NotewiseStroke] = []
    for object_payload in page_fields.get(4, []):
        if not isinstance(object_payload, bytes):
            continue
        outer = _message_values(object_payload)
        transform = outer.get(3, [None])[0]
        if 4 in outer:  # variable-width pen
            pen = _message_values(bytes(outer[4][0]))
            xs, ys, widths = _floats(pen.get(4, [None])[0]), _floats(pen.get(5, [None])[0]), _floats(pen.get(6, [None])[0])
            color, opacity = _style(pen.get(3, [None])[0])
            kind = "pen"
        elif 5 in outer:  # fixed-width highlighter
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


def render_notewise_ink(page_payload: bytes, size: tuple[int, int]) -> tuple[bytes, int]:
    strokes, (canvas_width, canvas_height) = read_notewise_strokes(page_payload)
    width, height = size
    scale_x, scale_y = width / canvas_width, height / canvas_height
    width_scale = (scale_x + scale_y) / 2
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    for stroke in strokes:
        rgb = ImageColor.getrgb(stroke.color)
        alpha = round(255 * stroke.opacity)
        points = [(x * scale_x, y * scale_y) for x, y in stroke.points]
        if len(points) == 1:
            radius = max(0.5, stroke.widths[0] * width_scale / 2)
            x, y = points[0]
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*rgb, alpha))
            continue
        for index in range(1, len(points)):
            segment_width = max(1, round((stroke.widths[index - 1] + stroke.widths[index]) * width_scale / 2))
            draw.line((points[index - 1], points[index]), fill=(*rgb, alpha), width=segment_width)
    output = BytesIO()
    layer.save(output, format="PNG")
    return output.getvalue(), len(strokes)
