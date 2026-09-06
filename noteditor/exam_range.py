"""족첵 안에서 **이 강의에 해당하는 쪽 범위**를 찾는다.

## 무엇을 푸는 문제인가

족첵(기출 모음)은 강의록 쪽을 그대로 싣고 그 **뒤에** 관련 문제를 붙인 문서다. 한 파일이
여러 강의를 이어 담고 있어서, 강의 하나를 처리하려면 그 안에서 이 강의 몫만 골라야 한다.
지금까지는 사람이 눈으로 찾아 쪽 번호를 입력했다.

핵심 관찰: **강의록 쪽의 그림이 족첵 안에 그대로 들어 있다.** 그러니 이건 의미를 읽는
문제가 아니라 그림을 맞추는 문제이고, :mod:`noteditor.page_match` 의 쪽 지문이 이미 그
일을 한다. LLM 이 필요 없다.

## 어떻게 정하는가

1. **족첵의 각 쪽을 가장 닮은 강의에 배정한다.** 이 강의만 보면 앞뒤 강의의 비슷한 쪽까지
   끌어온다(실측: 이웃 강의가 있는 파일에서 시작점이 15쪽 앞으로 밀렸다). 후보 강의를
   함께 놓고 겨루게 하면 그 경계가 저절로 선다.
2. **시작은 우리 쪽이 처음 나오는 자리.** 앞으로 늘리지 않는다 — 문제는 강의록 쪽 *뒤에*
   붙으므로, 우리 첫 쪽 앞의 미배정 쪽은 앞 강의의 문제다. 다만 **앞에 다른 강의가 하나도
   없으면** 문서 첫 쪽까지 늘린다(표지 등).
3. **끝은 우리 쪽 뒤에 붙은 문제까지.** 마지막으로 우리에게 배정된 쪽 다음부터, *다른*
   강의에 배정된 쪽이 나오기 직전까지 가져간다. 없으면 문서 끝까지.

## 실측

사용자가 손으로 고른 합치기 범위 **12건**(병리학·약리학, 족첵 36~253쪽, 후보 강의 5~9개)과
대조했다. **10건이 양끝까지 정확히 일치**했고 1건은 끝이 1쪽 넘쳤다. 한 파일당 3~9초.

## 하나 남은 한계 — 끝 경계는 다음 강의가 있어야 안다

12건 중 1건(약리학 2주차(2) / 노영윤 211쪽)은 정답 1-62 인데 1-211 을 제안했다. 그
족첵의 63쪽부터는 **아직 만들지 않은 다음 강의**의 몫이라 겨룰 상대가 없었고, 규칙이
문서 끝까지 가져갔다.

이건 코드로 못 막는다 — 그 쪽들이 누구 것인지 말해 줄 자료가 없기 때문이다. 대신
:attr:`RangeProposal.confidence` 가 그 경우 눈에 띄게 낮았다(0.21, 나머지 11건은 0.30~0.81).
**끝이 문서 끝까지 갔고 신뢰가 낮으면 화면에서 그렇게 말해야 한다.**

**제안일 뿐이다.** 사람이 화면에서 확인하고 고칠 수 있어야 한다 — 합치기 규격의 대전제
("판정은 자동, 갱신은 사람")를 여기서도 지킨다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .page_match import PageFingerprint, distance, fingerprints

# 같은 쪽 쌍의 거리는 최대 0.22, 서로 다른 쪽은 최소 0.78 이었다(page_match 의 실측). 그
# 사이 어디를 잡아도 되지만, 재조판·주석 때문에 조금 벌어지는 쪽을 놓치지 않게 위쪽에 둔다.
MATCH_THRESHOLD = 0.35


@dataclass(frozen=True)
class RangeProposal:
    """제안한 범위. 쪽 번호는 1-based, 양끝 포함."""

    first: int
    last: int
    matched_pages: tuple[int, ...]      # 실제로 강의록과 닮았던 쪽들
    page_count: int

    @property
    def pages(self) -> str:
        return f"{self.first}-{self.last}" if self.first != self.last else str(self.first)

    @property
    def confidence(self) -> float:
        """범위 안에서 강의록 쪽이 차지하는 비율. 낮으면 사람이 더 들여다볼 값이다.

        실측 12건에서 맞은 제안은 0.30~0.81, 유일하게 크게 빗나간 제안은 0.21 이었다.
        """
        span = self.last - self.first + 1
        return len(self.matched_pages) / span if span > 0 else 0.0

    @property
    def trailing_unmatched(self) -> int:
        """마지막으로 강의록과 닮았던 쪽 뒤로 몇 쪽을 더 가져왔는가(=문제 쪽으로 본 양)."""
        return self.last - (self.matched_pages[-1] if self.matched_pages else self.last)

    @property
    def runs_to_end(self) -> bool:
        return self.last >= self.page_count

    @property
    def uncertain(self) -> bool:
        """끝 경계를 믿기 어려운가 — 겨룰 다음 강의가 없어 문서 끝까지 갔을 때 그렇다.

        이 경우에만 화면이 "끝 쪽을 확인하세요"라고 말하면 된다. 나머지는 조용히 맞다.
        """
        return self.runs_to_end and (self.trailing_unmatched > len(self.matched_pages)
                                     or self.confidence < 0.25)


def _nearest(page: PageFingerprint, others: list[PageFingerprint]) -> float:
    return min((distance(page, other) for other in others), default=float("inf"))


def assign_owners(exam: list[PageFingerprint], lectures: list[list[PageFingerprint]],
                  threshold: float = MATCH_THRESHOLD) -> list[int | None]:
    """족첵 쪽마다 `lectures` 중 가장 닮은 것의 번호. 아무것도 안 닮았으면 None(문제 쪽)."""
    owners: list[int | None] = []
    for page in exam:
        best, best_distance = None, float("inf")
        for index, lecture in enumerate(lectures):
            value = _nearest(page, lecture)
            if value < best_distance:
                best, best_distance = index, value
        owners.append(best if best_distance < threshold else None)
    return owners


def range_from_owners(owners: list[int | None], mine: int = 0) -> RangeProposal | None:
    """배정 결과에서 내 범위를 뽑는다. 규칙은 이 모듈 docstring 참고."""
    matched = [index for index, owner in enumerate(owners) if owner == mine]
    if not matched:
        return None
    first, last = matched[0], matched[-1]

    # 앞: 나보다 앞에 **다른 강의**가 하나도 없으면 문서 첫 쪽까지. 있으면 그대로 둔다 —
    # 그 사이 미배정 쪽은 앞 강의의 문제이지 내 것이 아니다.
    if not any(owner is not None and owner != mine for owner in owners[:first]):
        first = 0

    # 뒤: 내 마지막 쪽에 붙은 문제까지. 다른 강의가 시작되면 거기서 끊는다.
    end = last
    for index in range(last + 1, len(owners)):
        owner = owners[index]
        if owner is not None and owner != mine:
            break
        end = index

    return RangeProposal(first + 1, end + 1,
                         tuple(index + 1 for index in matched), len(owners))


def locate(exam: Path, lecture: Path, others: list[Path] | None = None,
           threshold: float = MATCH_THRESHOLD) -> RangeProposal | None:
    """`exam` 안에서 `lecture` 에 해당하는 쪽 범위. 못 찾으면 None.

    `others` 는 같은 족첵을 나눠 쓰는 이웃 강의들의 강의록이다. 넣을수록 경계가 정확해진다.
    """
    from . import pdf as pymupdf

    def read(path: Path) -> list[PageFingerprint]:
        with pymupdf.open(Path(path)) as document:
            return fingerprints(document)

    exam_pages = read(exam)
    if not exam_pages:
        return None
    lectures = [read(lecture)] + [read(path) for path in (others or [])]
    if not lectures[0]:
        return None
    return range_from_owners(assign_owners(exam_pages, lectures, threshold))
