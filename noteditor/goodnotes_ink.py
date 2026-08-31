"""Goodnotes 페이지 저널에서 펜 획을 읽어 미리보기 레이어로 그린다.

저장에는 쓰지 않는다. 여기서 해석한 값으로 페이지를 다시 쓰지 않고, 사용자가 저장 전에
"필기가 새 배경 위 제자리에 오는지" 눈으로 확인할 투명 PNG만 만든다. 그래서 아직 뜻을 모르는
객체는 오류를 내지 않고 그냥 건너뛴다 — 미리보기에 안 보일 뿐 저장 결과에는 그대로 남는다.

획의 좌표는 Apple 프레임 LZ4 안에 ``tpl`` 블록으로 들어 있다. 블록은 맨 앞의 ASCII
**타입 서명** 이 뒤따르는 구역들의 형태를 알려주는 구조라서, 서명을 그대로 해석해 구역을
훑는다. 펜 종류마다 서명이 다르고 모르는 서명이 나올 수 있으므로, 아는 서명 세 가지만
그리고 나머지는 건너뛴다.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageDraw

from .goodnotes_proto import apple_lz4_decompress, field_values, split_delimited

# 서명마다 "획의 경로가 몇 번째 구역인지"가 다르다. 실제 Goodnotes 6 내보내기에서 확인한
# 세 가지만 그린다. 값은 (경로 구역 번호, 항목당 실수 개수, 펜 종류).
_PRESSURE_SIGNATURE = "vA(v)A(u)A(u)A(v)A(v)A(u)A(u)A(u)A(u)A(v)"
_CONSTANT_SIGNATURE = "vuA(v)A(S(uu))A(S(uuuu))vA(f)"
_PENCIL_SIGNATURE = (
    "vuA(v)A(S(uuuuu))A(S(uuuuuuuuuuu))A(S(uu))A(v)A(S(uu))A(S(uuuu))A(u)"
)

_SCALAR_SIZES = {"v": 2, "u": 4}
_SCALAR_FORMATS = {"v": "<H", "u": "<f"}


@dataclass(frozen=True)
class GoodnotesStroke:
    kind: str
    points: tuple[tuple[float, float], ...]
    widths: tuple[float, ...]
    color: tuple[int, int, int]
    opacity: float


@dataclass(frozen=True)
class _Section:
    """서명이 알려준 한 구역. ``stride`` 는 항목 하나가 몇 개의 값으로 이루어지는지다."""

    values: tuple[float, ...]
    stride: int


def _tokenize(signature: str) -> list[tuple[str, tuple[str, ...]]] | None:
    """서명을 ``("scalar", ("u",))`` / ``("array", ("u", "u"))`` 목록으로 나눈다."""
    tokens: list[tuple[str, tuple[str, ...]]] = []
    index = 0
    while index < len(signature):
        char = signature[index]
        if char in "vuf":
            tokens.append(("scalar", (char,)))
            index += 1
            continue
        if char != "A" or signature[index + 1: index + 2] != "(":
            return None
        index += 2
        if signature[index: index + 2] == "S(":
            index += 2
            end = signature.find(")", index)
            if end < 0:
                return None
            item = tuple(signature[index:end])
            index = end + 1
        else:
            item = (signature[index],)
            index += 1
        if signature[index: index + 1] != ")":
            return None
        index += 1
        if any(scalar not in "vuf" for scalar in item):
            return None
        tokens.append(("array", item))
    return tokens


def _read_scalars(data: bytes, offset: int, item: tuple[str, ...], count: int):
    values: list[float] = []
    for _ in range(count):
        for scalar in item:
            size = _SCALAR_SIZES.get(scalar)
            if size is None:  # 'f' 는 항목 수가 0일 때만 관측됐다 — 크기를 모른다.
                raise ValueError("크기를 모르는 구역")
            end = offset + size
            if end > len(data):
                raise ValueError("구역이 잘림")
            values.append(float(struct.unpack(_SCALAR_FORMATS[scalar], data[offset:end])[0]))
            offset = end
    return values, offset


def _parse_tpl(
    blob: bytes,
) -> tuple[str, list[tuple[str, float]], list[_Section]] | None:
    """``tpl`` 블록을 (서명, 앞쪽 스칼라들, 구역들)로 푼다. 못 읽으면 ``None``.

    스칼라는 종류를 붙여 돌려준다. 굵기가 일정한 펜의 서명은 ``vu`` 로 시작하는데 앞의
    ``v`` 는 u16이고 굵기는 뒤의 float32라서, 순서만 보고 집으면 굵기 대신 엉뚱한 수를
    읽는다.
    """
    if not blob.startswith(b"tpl\0") or len(blob) < 8:
        return None
    end = blob.find(b"\0", 8)
    if end < 0:
        return None
    try:
        signature = blob[8:end].decode("ascii")
    except UnicodeDecodeError:
        return None
    tokens = _tokenize(signature)
    if tokens is None:
        return None
    offset = end + 1
    scalars: list[tuple[str, float]] = []
    sections: list[_Section] = []
    try:
        for kind, item in tokens:
            if kind == "scalar":
                values, offset = _read_scalars(blob, offset, item, 1)
                scalars.extend(zip(item, values))
                continue
            if offset + 4 > len(blob):
                return None
            (count,) = struct.unpack_from("<I", blob, offset)
            offset += 4
            if count and any(scalar == "f" for scalar in item):
                return None
            values, offset = _read_scalars(blob, offset, item, count)
            sections.append(_Section(tuple(values), len(item)))
    except (ValueError, struct.error):
        return None
    return signature, scalars, sections


def _split_subpaths(
    points: list[tuple[float, float]], widths: list[float]
) -> list[tuple[list[tuple[float, float]], list[float]]]:
    """원점 근처의 표식 점에서 획을 끊는다. 안 끊으면 화면을 가로지르는 줄이 생긴다."""
    runs: list[tuple[list[tuple[float, float]], list[float]]] = []
    current_points: list[tuple[float, float]] = []
    current_widths: list[float] = []
    for (x, y), width in zip(points, widths):
        if abs(x) < 1e-3 and abs(y) < 1e-3:
            if current_points:
                runs.append((current_points, current_widths))
            current_points, current_widths = [], []
            continue
        current_points.append((x, y))
        current_widths.append(width)
    if current_points:
        runs.append((current_points, current_widths))
    return runs


def _pressure_path(sections: list[_Section]) -> tuple[list[tuple[float, float]], list[float]]:
    """압력 펜의 경로. 첫 구역의 플래그가 경로 배치를 고른다."""
    if len(sections) < 3:
        return [], []
    flags = sections[0].values
    path = sections[2].values
    # 플래그 비트 2가 서면 (x1,y1,w1,x2,y2,w2,기울기1,기울기2,상수)로 두 점씩 묶여 있다.
    # 서지 않으면 (x,y,굵기) 세 값이 한 점이다. 9의 배수는 3의 배수이기도 해서 플래그를
    # 안 보면 기울기 열이 유령 점으로 섞여 들어온다.
    paired = bool(flags) and int(flags[0]) & 0x4
    points: list[tuple[float, float]] = []
    widths: list[float] = []
    if paired and len(path) >= 9:
        for start in range(0, len(path) - 8, 9):
            points.append((path[start], path[start + 1]))
            widths.append(path[start + 2])
            points.append((path[start + 3], path[start + 4]))
            widths.append(path[start + 5])
    else:
        for start in range(0, len(path) - 2, 3):
            points.append((path[start], path[start + 1]))
            widths.append(path[start + 2])
    return points, widths


def _segment_path(
    section: _Section, width: float, columns: tuple[int, int, int, int]
) -> tuple[list[tuple[float, float]], list[float]]:
    """이어 붙은 선분 목록을 점의 나열로 편다. 선분의 끝과 다음 시작은 거의 붙어 있다."""
    first_x, first_y, second_x, second_y = columns
    stride = section.stride
    values = section.values
    points: list[tuple[float, float]] = []
    for start in range(0, len(values) - stride + 1, stride):
        head = (values[start + first_x], values[start + first_y])
        tail = (values[start + second_x], values[start + second_y])
        if not points:
            points.append(head)
        elif abs(points[-1][0] - head[0]) > 0.5 or abs(points[-1][1] - head[1]) > 0.5:
            points.append(head)
        points.append(tail)
    return points, [width] * len(points)


def _pen_width(scalars: list[tuple[str, float]]) -> float:
    """굵기가 일정한 펜은 서명 앞의 float32 스칼라가 굵기(포인트)다."""
    return next((value for code, value in scalars if code == "u"), 1.0)


def _stroke_color(stroke: dict) -> tuple[tuple[int, int, int], float]:
    """색은 float32 R·G·B·A다. 빠진 항목은 0.0이라 검은 펜은 알파만 들어 있다."""
    values = stroke.get(4)
    if not values or not isinstance(values[0], bytes):
        return (0, 0, 0), 1.0
    channels = field_values(bytes(values[0]))
    def channel(number: int, default: float) -> float:
        payload = channels.get(number)
        if not payload or not isinstance(payload[0], bytes) or len(payload[0]) != 4:
            return default
        return float(struct.unpack("<f", bytes(payload[0]))[0])

    red, green, blue = channel(1, 0.0), channel(2, 0.0), channel(3, 0.0)
    alpha = channel(4, 1.0)
    clamp = lambda value: max(0, min(255, round(value * 255)))
    return (clamp(red), clamp(green), clamp(blue)), max(0.0, min(1.0, alpha))


def _stroke_kind(stroke: dict) -> str:
    if stroke.get(5):
        return "highlighter"
    if 20 in stroke and bytes(stroke[20][0] or b""):
        return "marker"
    style = stroke.get(3, [None])[0]
    if style == 5:
        return "pencil"
    if style == 1:
        return "pen"
    return "ball"


def read_goodnotes_strokes(page_payload: bytes) -> tuple[GoodnotesStroke, ...]:
    """페이지 저널에서 그릴 수 있는 획만 골라 준다.

    스키마 25의 페이지는 (머리말, 본문) 두 레코드가 짝을 이루는 저널이고, 스키마 24는
    평평한 스트림이다. 어느 쪽이든 **본문 레코드의 7번 필드**가 획이므로 그것만 훑으면
    두 형태를 따로 다룰 필요가 없다.
    """
    strokes: list[GoodnotesStroke] = []
    for record in split_delimited(page_payload):
        fields = field_values(record)
        payload = fields.get(7)
        if not payload or not isinstance(payload[0], bytes):
            continue
        stroke = field_values(bytes(payload[0]))
        geometry = stroke.get(2)
        if not geometry or not isinstance(geometry[0], bytes) or not geometry[0]:
            continue
        try:
            blob = apple_lz4_decompress(bytes(geometry[0]))
        except Exception:
            continue
        parsed = _parse_tpl(blob)
        if parsed is None:
            continue
        signature, scalars, sections = parsed
        kind = _stroke_kind(stroke)
        color, opacity = _stroke_color(stroke)
        if signature == _PRESSURE_SIGNATURE:
            points, widths = _pressure_path(sections)
        elif signature == _CONSTANT_SIGNATURE and len(sections) >= 3:
            points, widths = _segment_path(sections[2], _pen_width(scalars), (0, 1, 2, 3))
        elif signature == _PENCIL_SIGNATURE and len(sections) >= 3:
            points, widths = _segment_path(sections[2], _pen_width(scalars), (1, 2, 6, 7))
        else:
            continue
        for run_points, run_widths in _split_subpaths(points, widths):
            strokes.append(
                GoodnotesStroke(kind, tuple(run_points), tuple(run_widths), color, opacity)
            )
    return tuple(strokes)


def render_goodnotes_ink(
    page_payload: bytes, size: tuple[int, int], canvas: tuple[float, float]
) -> tuple[bytes, int]:
    """페이지 캔버스 좌표의 획을 미리보기 그림 크기에 맞춰 투명 PNG로 그린다."""
    strokes = read_goodnotes_strokes(page_payload)
    width, height = size
    canvas_width = max(1.0, float(canvas[0]))
    canvas_height = max(1.0, float(canvas[1]))
    scale_x, scale_y = width / canvas_width, height / canvas_height
    width_scale = (scale_x + scale_y) / 2
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    for stroke in strokes:
        alpha = round(255 * stroke.opacity)
        fill = (*stroke.color, alpha)
        points = [(x * scale_x, y * scale_y) for x, y in stroke.points]
        if len(points) == 1:
            radius = max(0.5, stroke.widths[0] * width_scale / 2)
            x, y = points[0]
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)
            continue
        for index in range(1, len(points)):
            segment = (stroke.widths[index - 1] + stroke.widths[index]) / 2
            draw.line(
                (points[index - 1], points[index]),
                fill=fill,
                width=max(1, round(segment * width_scale)),
            )
    output = BytesIO()
    layer.save(output, format="PNG")
    return output.getvalue(), len(strokes)


def count_goodnotes_strokes(page_payload: bytes) -> int:
    """미리보기와 무관하게 "이 쪽에 필기가 있는가"를 세는 값.

    그릴 수 없는 획도 필기는 필기다. 그래서 도형을 해석하지 않고 획 레코드 수만 센다.
    """
    total = 0
    for record in split_delimited(page_payload):
        fields = field_values(record)
        payload = fields.get(7)
        if payload and isinstance(payload[0], bytes):
            total += 1
    return total


__all__ = [
    "GoodnotesStroke",
    "count_goodnotes_strokes",
    "read_goodnotes_strokes",
    "render_goodnotes_ink",
]
