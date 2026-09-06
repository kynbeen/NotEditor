"""족첵에서 이 강의에 해당하는 쪽 범위 짚기.

배정 결과에서 범위를 뽑는 규칙만 따로 시험한다 — 지문 계산은 :mod:`noteditor.page_match`
가 이미 자기 시험을 갖고 있고, 여기서 정해지는 것은 **경계를 어디로 볼 것인가**다.
"""
from __future__ import annotations

import unittest
import unittest.mock

from noteditor.exam_range import RangeProposal, assign_owners, range_from_owners


class RangeRuleTests(unittest.TestCase):
    def test_range_starts_at_my_first_page_when_another_lecture_came_before(self):
        """내 첫 쪽 앞의 미배정 쪽은 **앞 강의의 문제**다. 문제는 강의록 쪽 뒤에 붙는다."""
        owners = [1, 1, None, None, 0, 0, None, None]
        got = range_from_owners(owners)
        self.assertEqual((5, 8), (got.first, got.last))

    def test_range_reaches_the_first_page_when_nothing_precedes_me(self):
        """앞에 겨룰 강의가 없으면 앞의 미배정 쪽(표지 등)도 내 것이다."""
        owners = [None, None, 0, 0, None]
        got = range_from_owners(owners)
        self.assertEqual((1, 5), (got.first, got.last))

    def test_trailing_question_pages_belong_to_the_lecture_before_them(self):
        owners = [0, 0, None, None, None, 1, 1]
        got = range_from_owners(owners)
        self.assertEqual((1, 5), (got.first, got.last))

    def test_a_lecture_that_never_appears_gets_no_range(self):
        self.assertIsNone(range_from_owners([1, 1, None, 2]))

    def test_matched_pages_are_reported_one_based(self):
        got = range_from_owners([None, 0, None, 0])
        self.assertEqual((2, 4), got.matched_pages)


class ConfidenceTests(unittest.TestCase):
    def test_confidence_is_the_share_of_pages_that_looked_like_the_lecture(self):
        got = RangeProposal(first=1, last=10, matched_pages=(1, 2, 3, 4, 5), page_count=10)
        self.assertAlmostEqual(0.5, got.confidence)

    def test_a_range_that_runs_to_the_end_with_a_long_tail_is_flagged(self):
        """겨룰 다음 강의가 없어 문서 끝까지 간 경우다 — 실측에서 유일하게 빗나간 모양."""
        got = RangeProposal(first=1, last=211, matched_pages=tuple(range(3, 63)),
                            page_count=211)
        self.assertTrue(got.uncertain)
        self.assertEqual(211 - 62, got.trailing_unmatched)

    def test_a_range_that_ends_before_another_lecture_is_not_flagged(self):
        got = RangeProposal(first=1, last=63, matched_pages=tuple(range(2, 61)),
                            page_count=88)
        self.assertFalse(got.uncertain)
        self.assertFalse(got.runs_to_end)

    def test_pages_reads_as_one_number_for_a_single_page_range(self):
        self.assertEqual("7", RangeProposal(7, 7, (7,), 20).pages)
        self.assertEqual("7-9", RangeProposal(7, 9, (7,), 20).pages)


class OwnerAssignmentTests(unittest.TestCase):
    """겨루기 — 이 강의만 보면 이웃 강의의 닮은 쪽까지 끌어온다."""

    class _Fingerprint:
        """`page_match.distance` 대신 쓰는 최소 대역. 셀 하나로 거리를 조종한다."""

        def __init__(self, value: float):
            self.cells = (value,)
            self.aspect = 1.0
            self.blank = False

    def setUp(self):
        self.patcher = unittest.mock.patch(
            "noteditor.exam_range.distance",
            lambda left, right: abs(left.cells[0] - right.cells[0]))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_each_page_goes_to_the_lecture_it_looks_most_like(self):
        exam = [self._Fingerprint(v) for v in (0.0, 0.05, 5.0, 9.9, 10.0)]
        mine = [self._Fingerprint(0.0)]
        neighbour = [self._Fingerprint(10.0)]
        self.assertEqual([0, 0, None, 1, 1],
                         assign_owners(exam, [mine, neighbour], threshold=0.35))

    def test_pages_that_look_like_nothing_are_question_pages(self):
        exam = [self._Fingerprint(v) for v in (0.0, 4.0)]
        self.assertEqual([0, None],
                         assign_owners(exam, [[self._Fingerprint(0.0)]], threshold=0.35))


if __name__ == "__main__":
    unittest.main()
