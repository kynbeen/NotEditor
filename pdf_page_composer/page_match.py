"""두 PDF의 공통 쪽을, 순서를 지키면서 짝짓는다.

개정판 PDF에는 쪽이 끼어들거나 빠져 있을 수 있고 끼어든 쪽의 내용은 두 문서에서 서로 다르다.
그래도 **공통 쪽의 순서는 바뀌지 않는다**는 것이 사용자가 확정한 전제다. 이 전제 덕분에 문제가
순서 보존 시퀀스 정렬(Needleman-Wunsch식 DP)이 되고, 삽입·삭제는 갭으로 처리된다.

순서 조건은 편의가 아니라 정확도의 근거다. 실제 강의 슬라이드에는 거의 똑같이 생긴 쪽이 있어서
(샘플에서 27·28쪽) 생김새만 비교하면 뒤집힐 수 있는데, 앞뒤 순서가 그 애매함을 해소한다.

쪽 지문은 본문 상자를 잘라 고정 격자로 정규화한 밝기 벡터다. 본문 상자로 자르는 순간 확대/축소와
여백 변화가 상쇄되므로, 배경을 본문 기준으로 다시 앉히는 :mod:`alignment` 와 함께 써도 서로
방해하지 않는다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .alignment import ink_box

_GRID = 16
_SUPERSAMPLE = 8
_FINGERPRINT_MAX_SIDE = 500
_ASPECT_WEIGHT = 0.35
# 실제 50쪽 슬라이드로 교정했다. 재조판된 판본에서 정답 짝의 거리는 최대 0.22, 서로 다른 쪽은
# 최소 0.78이었다. 갭 비용 0.35 는 "거리가 0.70을 넘으면 짝짓느니 양쪽에 갭을 둔다"는 뜻이다.
_GAP_COST = 0.35
_EMPTY_DISTANCE = 1.6
_UNCERTAIN_DISTANCE = 0.35
_UNCERTAIN_MARGIN = 0.08
_MAX_CELLS = 400_000


class PageMatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class PageFingerprint:
    """한 쪽의 생김새. ``cells`` 가 비어 있으면 잉크가 없는 빈 쪽이다."""

    index: int
    cells: tuple[float, ...]
    aspect: float

    @property
    def blank(self) -> bool:
        return not self.cells


@dataclass(frozen=True)
class PagePair:
    """결과 문서의 한 칸. 한쪽이 ``None`` 이면 그쪽에만 있는 쪽이다."""

    source_index: int | None
    target_index: int | None
    distance: float | None = None
    margin: float | None = None

    @property
    def matched(self) -> bool:
        return self.source_index is not None and self.target_index is not None

    @property
    def confident(self) -> bool:
        if not self.matched:
            return True
        if self.distance is None or self.distance > _UNCERTAIN_DISTANCE:
            return False
        return self.margin is None or self.margin >= _UNCERTAIN_MARGIN

    def as_dict(self) -> dict:
        return {
            "source_index": self.source_index,
            "target_index": self.target_index,
            "distance": None if self.distance is None else round(self.distance, 4),
            "margin": None if self.margin is None else round(self.margin, 4),
            "matched": self.matched,
            "confident": self.confident,
        }


@dataclass(frozen=True)
class MatchResult:
    pairs: tuple[PagePair, ...]

    @property
    def matched_pairs(self) -> tuple[PagePair, ...]:
        return tuple(pair for pair in self.pairs if pair.matched)

    @property
    def source_only(self) -> tuple[int, ...]:
        return tuple(p.source_index for p in self.pairs if p.target_index is None)

    @property
    def target_only(self) -> tuple[int, ...]:
        return tuple(p.target_index for p in self.pairs if p.source_index is None)

    @property
    def uncertain(self) -> tuple[PagePair, ...]:
        return tuple(pair for pair in self.pairs if pair.matched and not pair.confident)

    def source_to_target(self) -> dict[int, int]:
        return {p.source_index: p.target_index for p in self.matched_pairs}

    def as_dict(self) -> dict:
        return {
            "pairs": [pair.as_dict() for pair in self.pairs],
            "matched_count": len(self.matched_pairs),
            "source_only": list(self.source_only),
            "target_only": list(self.target_only),
            "uncertain_count": len(self.uncertain),
        }


def fingerprint(page, grid: int = _GRID) -> PageFingerprint:
    """본문 상자를 ``grid``×``grid`` 로 정규화한 밝기 벡터. 배율·여백에 영향받지 않는다."""
    import pymupdf

    box = ink_box(page, max_side=_FINGERPRINT_MAX_SIDE)
    if box is None or box.width < 1 or box.height < 1:
        return PageFingerprint(index=-1, cells=(), aspect=1.0)
    # 셀마다 픽셀 하나를 찍으면 배율이 조금만 달라져도 값이 튄다. 넉넉히 그린 뒤 셀 단위로
    # 평균을 내야 재조판된 판본과도 거리가 가깝게 나온다.
    span = grid * _SUPERSAMPLE
    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(span / box.width, span / box.height),
        clip=box,
        colorspace=pymupdf.csGRAY,
        alpha=False,
    )
    width, height, samples = pixmap.width, pixmap.height, pixmap.samples
    if width < grid or height < grid:
        return PageFingerprint(index=-1, cells=(), aspect=1.0)
    values = []
    for row in range(grid):
        top = row * height // grid
        bottom = max(top + 1, (row + 1) * height // grid)
        for column in range(grid):
            left = column * width // grid
            right = max(left + 1, (column + 1) * width // grid)
            total = 0
            for y in range(top, bottom):
                start = y * width
                total += sum(samples[start + left:start + right])
            values.append(total / ((bottom - top) * (right - left)))
    average = sum(values) / len(values)
    variance = sum((value - average) ** 2 for value in values) / len(values)
    spread = math.sqrt(variance) or 1.0
    cells = tuple((value - average) / spread for value in values)
    return PageFingerprint(index=-1, cells=cells, aspect=box.width / box.height)


def fingerprints(document, grid: int = _GRID) -> list[PageFingerprint]:
    result = []
    for index in range(document.page_count):
        base = fingerprint(document[index], grid)
        result.append(PageFingerprint(index=index, cells=base.cells, aspect=base.aspect))
    return result


def distance(left: PageFingerprint, right: PageFingerprint) -> float:
    """0에 가까울수록 같은 쪽. 빈 쪽끼리는 서로 닮은 것으로 본다."""
    if left.blank or right.blank:
        return 0.0 if left.blank and right.blank else _EMPTY_DISTANCE
    total = 0.0
    for a, b in zip(left.cells, right.cells):
        total += a - b if a > b else b - a
    shape = total / len(left.cells)
    ratio = abs(math.log(left.aspect / right.aspect)) if right.aspect > 0 else 1.0
    return shape + _ASPECT_WEIGHT * ratio


def _distance_matrix(
    source: list[PageFingerprint], target: list[PageFingerprint]
) -> list[list[float]]:
    if len(source) * len(target) > _MAX_CELLS:
        raise PageMatchError(
            f"쪽이 너무 많아 자동 매칭을 할 수 없습니다: {len(source)}쪽 × {len(target)}쪽"
        )
    return [[distance(left, right) for right in target] for left in source]


def _margin(matrix: list[list[float]], row: int, column: int) -> float | None:
    """짝지어진 상대 말고 두 번째로 닮은 후보와의 거리 차이. 작을수록 애매하다."""
    best = math.inf
    for other in range(len(matrix[row])):
        if other != column:
            best = min(best, matrix[row][other])
    for other in range(len(matrix)):
        if other != row:
            best = min(best, matrix[other][column])
    if math.isinf(best):
        return None
    return best - matrix[row][column]


def match_pages(
    source_document, target_document, gap_cost: float = _GAP_COST
) -> MatchResult:
    """공통 쪽을 순서를 지키며 짝짓는다. 삽입·삭제는 갭으로 남는다."""
    source = fingerprints(source_document)
    target = fingerprints(target_document)
    return match_fingerprints(source, target, gap_cost)


def match_fingerprints(
    source: list[PageFingerprint], target: list[PageFingerprint], gap_cost: float = _GAP_COST
) -> MatchResult:
    matrix = _distance_matrix(source, target)
    rows, columns = len(source), len(target)

    # cost[i][j] = 앞의 i쪽과 j쪽까지 정렬했을 때의 최소 비용. choice 에 어떤 수를 골랐는지
    # 같이 적어둔다 (역추적에서 부동소수점 비교를 하지 않으려고).
    _MATCH, _SKIP_SOURCE, _SKIP_TARGET = 0, 1, 2
    cost = [[0.0] * (columns + 1) for _ in range(rows + 1)]
    choice = [[_MATCH] * (columns + 1) for _ in range(rows + 1)]
    for i in range(1, rows + 1):
        cost[i][0] = i * gap_cost
        choice[i][0] = _SKIP_SOURCE
    for j in range(1, columns + 1):
        cost[0][j] = j * gap_cost
        choice[0][j] = _SKIP_TARGET
    for i in range(1, rows + 1):
        row_above, row_here, row_choice = cost[i - 1], cost[i], choice[i]
        distances = matrix[i - 1]
        for j in range(1, columns + 1):
            matched = row_above[j - 1] + distances[j - 1]
            skip_source = row_above[j] + gap_cost
            skip_target = row_here[j - 1] + gap_cost
            best = matched
            taken = _MATCH
            if skip_source < best:
                best, taken = skip_source, _SKIP_SOURCE
            if skip_target < best:
                best, taken = skip_target, _SKIP_TARGET
            row_here[j] = best
            row_choice[j] = taken

    pairs: list[PagePair] = []
    i, j = rows, columns
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            taken = choice[i][j]
        elif i > 0:
            taken = _SKIP_SOURCE
        else:
            taken = _SKIP_TARGET
        if taken == _MATCH:
            pairs.append(
                PagePair(
                    source_index=i - 1,
                    target_index=j - 1,
                    distance=matrix[i - 1][j - 1],
                    margin=_margin(matrix, i - 1, j - 1),
                )
            )
            i -= 1
            j -= 1
        elif taken == _SKIP_SOURCE:
            pairs.append(PagePair(source_index=i - 1, target_index=None))
            i -= 1
        else:
            pairs.append(PagePair(source_index=None, target_index=j - 1))
            j -= 1
    pairs.reverse()
    return MatchResult(pairs=tuple(pairs))
