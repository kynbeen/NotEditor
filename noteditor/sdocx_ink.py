"""Render Samsung Notes stroke objects as a transparent preview layer.

The object and compact-stroke decoding in this module is adapted from the
MIT-licensed Dietrich Samsung Notes parser.  See ``THIRD_PARTY_NOTICES.md``.
Only read-only preview rendering lives here; saved ``.page`` bytes are never
rewritten from these decoded values.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageDraw

from .sdocx_page import read_page


@dataclass(frozen=True)
class InkStroke:
    points: tuple[tuple[float, float], ...]
    color: tuple[int, int, int, int]
    pen_size: float


def _can_read(blob: bytes, offset: int, size: int) -> bool:
    return offset >= 0 and size >= 0 and offset + size <= len(blob)


def _unpack_delta(value: int) -> float:
    magnitude = ((value >> 5) & 0x3FF) + (value & 0x1F) / 32.0
    return -magnitude if value & 0x8000 else magnitude


def _read_var_uint(blob: bytes, offset: int, size: int, limit: int) -> int | None:
    if size < 0 or size > 4 or offset < 0 or offset + size > limit:
        return None
    return int.from_bytes(blob[offset:offset + size], "little") if size else 0


def _argb_to_rgba(value: int) -> tuple[int, int, int, int]:
    alpha = (value >> 24) & 0xFF
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF, alpha)


def _subrecords(blob: bytes, start: int, end: int) -> list[tuple[int, int, int]]:
    records: list[tuple[int, int, int]] = []
    position = start
    while position + 6 <= end:
        size, record_type = struct.unpack_from("<IH", blob, position)
        if size <= 0 or position + size > end:
            break
        records.append((record_type, position, position + size))
        position += size
    return records


def _object_record(blob: bytes, position: int) -> tuple[dict | None, int]:
    if not _can_read(blob, position, 7):
        return None, position
    object_type = blob[position]
    child_count = struct.unpack_from("<H", blob, position + 1)[0]
    object_size = struct.unpack_from("<I", blob, position + 3)[0]
    if object_size < 32:
        return None, position
    payload_start = position + 7
    payload_end = payload_start + object_size - 32
    record_end = payload_start + object_size
    if payload_end < payload_start or record_end > len(blob):
        return None, position
    record = {
        "type": object_type,
        "subrecords": _subrecords(blob, payload_start, payload_end),
        "children": [],
    }
    cursor = record_end
    for _ in range(child_count):
        child, next_cursor = _object_record(blob, cursor)
        if child is None or next_cursor <= cursor:
            return None, position
        record["children"].append(child)
        cursor = next_cursor
    return record, cursor


def _flat_objects(record: dict):
    yield record
    for child in record["children"]:
        yield from _flat_objects(child)


def _objects(blob: bytes, layer_offset: int):
    if not _can_read(blob, layer_offset, 4):
        return
    layer_count, current_layer = struct.unpack_from("<HH", blob, layer_offset)
    if not 1 <= layer_count <= 64 or current_layer >= layer_count:
        return
    position = layer_offset + 4
    for _ in range(layer_count):
        if not _can_read(blob, position, 16):
            return
        layer_start = position
        header_size = struct.unpack_from("<I", blob, position)[0]
        if not 16 <= header_size <= 0x4000:
            return
        count_at = layer_start + header_size
        if not _can_read(blob, count_at, 4):
            return
        object_count = struct.unpack_from("<I", blob, count_at)[0]
        if object_count > 4096:
            return
        cursor = count_at + 4
        for _ in range(object_count):
            record, next_cursor = _object_record(blob, cursor)
            if record is None or next_cursor <= cursor:
                return
            yield from _flat_objects(record)
            cursor = next_cursor
        position = cursor + 32


def _compact_geometry(
    blob: bytes, cursor: int, limit: int, point_count: int, optional_axes: bool
) -> tuple[tuple[tuple[float, float], ...], int] | None:
    if point_count <= 0 or not _can_read(blob, cursor, 16):
        return None
    x, y = struct.unpack_from("<dd", blob, cursor)
    points = [(x, y)]
    cursor += 16
    delta_bytes = max(0, point_count - 1) * 4
    if cursor + delta_bytes > limit or not _can_read(blob, cursor, delta_bytes):
        return None
    for _ in range(point_count - 1):
        dx, dy = struct.unpack_from("<HH", blob, cursor)
        x += _unpack_delta(dx)
        y += _unpack_delta(dy)
        points.append((x, y))
        cursor += 4

    # pressure: f32 seed + u16 deltas; timestamps: i32 seed + u16 deltas
    dynamic_size = (4 + max(0, point_count - 1) * 2) * 2
    if optional_axes:
        dynamic_size += (4 + max(0, point_count - 1) * 2) * 2
    cursor += dynamic_size
    if cursor + 2 > limit:
        return None
    return tuple(points), cursor + 2


def _raw_geometry(
    blob: bytes, cursor: int, limit: int, point_count: int, optional_axes: bool
) -> tuple[tuple[tuple[float, float], ...], int] | None:
    axes = point_count * 8 if optional_axes else 0
    available = limit - cursor
    f32_size = point_count * 8 + point_count * 8 + axes + 2
    f64_size = point_count * 16 + point_count * 8 + axes + 2
    if available == f32_size:
        stride, fmt = 8, "<ff"
    elif available == f64_size:
        stride, fmt = 16, "<dd"
    else:
        return None
    points = tuple(
        struct.unpack_from(fmt, blob, cursor + index * stride)
        for index in range(point_count)
    )
    return points, limit


def _stroke_from_object(blob: bytes, record: dict) -> InkStroke | None:
    if record["type"] not in (1, 15):
        return None
    stroke_record = next((item for item in record["subrecords"] if item[0] == 1), None)
    if stroke_record is None:
        return None
    _, start, end = stroke_record
    if start + 20 > end:
        return None
    flexible_offset = struct.unpack_from("<I", blob, start + 6)[0]
    mask1_size = blob[start + 10]
    mask1 = _read_var_uint(blob, start + 11, mask1_size, end)
    if mask1 is None:
        return None
    mask2_size_at = start + 11 + mask1_size
    if mask2_size_at >= end:
        return None
    mask2_size = blob[mask2_size_at]
    mask2 = _read_var_uint(blob, mask2_size_at + 1, mask2_size, end)
    if mask2 is None:
        return None
    cursor = mask2_size_at + 1 + mask2_size
    if cursor + 2 > end:
        return None
    point_count = struct.unpack_from("<H", blob, cursor)[0]
    cursor += 2
    geometry_end = start + flexible_offset
    if geometry_end < cursor or geometry_end > end or point_count > 100000:
        return None
    geometry = (
        _compact_geometry(blob, cursor, geometry_end, point_count, bool(mask1 & 0x0004))
        if mask1 & 0x0001
        else _raw_geometry(blob, cursor, geometry_end, point_count, bool(mask1 & 0x0004))
    )
    if geometry is None:
        return None
    points, _ = geometry

    color = 0xFF000000
    pen_size = 2.0
    flexible = geometry_end
    if mask2 & 0x0002:
        flexible += 4
    if mask2 & 0x0004 and flexible + 4 <= end:
        color = struct.unpack_from("<I", blob, flexible)[0]
        flexible += 4
    if mask2 & 0x0008 and flexible + 4 <= end:
        pen_size = struct.unpack_from("<f", blob, flexible)[0]
    return InkStroke(points, _argb_to_rgba(color), pen_size)


def _fallback_geometry(blob: bytes, start: int) -> list[InkStroke]:
    """Recover compact point records from older pages without decoded objects."""
    strokes: list[InkStroke] = []
    position = max(0, start)
    limit = len(blob) - 40
    while position < limit:
        point_count = struct.unpack_from("<H", blob, position)[0]
        if not 1 < point_count < 10000 or not _can_read(blob, position + 2, 16):
            position += 2
            continue
        x, y = struct.unpack_from("<dd", blob, position + 2)
        record_end = position + 18 + (point_count - 1) * 4
        if not (math.isfinite(x) and math.isfinite(y) and 1 < x < 2400 and 1 < y < 2400
                and record_end <= len(blob)):
            position += 2
            continue
        points = [(x, y)]
        cursor = position + 18
        for _ in range(point_count - 1):
            dx, dy = struct.unpack_from("<HH", blob, cursor)
            x += _unpack_delta(dx)
            y += _unpack_delta(dy)
            points.append((x, y))
            cursor += 4
        strokes.append(InkStroke(tuple(points), (0, 0, 0, 255), 2.0))
        position = record_end
    return strokes


def read_ink_strokes(page_blob: bytes) -> tuple[int, int, tuple[InkStroke, ...]]:
    """Return canvas dimensions and the stroke paths found in a ``.page`` blob."""
    info = read_page(page_blob)
    strokes = [
        stroke
        for record in _objects(page_blob, info.layer_offset)
        if (stroke := _stroke_from_object(page_blob, record)) is not None
    ]
    if not strokes:
        strokes = _fallback_geometry(page_blob, info.layer_offset)
    return info.canvas_width, info.canvas_height, tuple(strokes)


def render_ink_png(page_blob: bytes | None, width: int, height: int) -> tuple[bytes, int]:
    """Render strokes to a transparent PNG matching a PDF preview's pixel size."""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    count = 0
    if page_blob:
        canvas_width, canvas_height, strokes = read_ink_strokes(page_blob)
        if canvas_width > 0 and canvas_height > 0:
            scale_x = width / canvas_width
            scale_y = height / canvas_height
            scale = min(scale_x, scale_y)
            draw = ImageDraw.Draw(image, "RGBA")
            for stroke in strokes:
                points = [
                    (round(x * scale_x, 2), round(y * scale_y, 2))
                    for x, y in stroke.points
                    if math.isfinite(x) and math.isfinite(y)
                ]
                if len(points) < 2:
                    continue
                red, green, blue, alpha = stroke.color
                # Very pale or transparent ink disappears on white PDF previews.
                # Keep its hue, but apply a conservative visibility floor.
                visible_alpha = max(90, alpha)
                line_width = max(1, min(40, round(max(1.0, stroke.pen_size) * scale)))
                draw.line(points, fill=(red, green, blue, visible_alpha), width=line_width, joint="curve")
                count += 1
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue(), count
