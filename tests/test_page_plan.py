from __future__ import annotations

import unittest

from noteditor.page_match import MatchResult, PagePair
from noteditor.page_plan import PagePlan, PagePlanError


def automatic_match() -> MatchResult:
    return MatchResult((
        PagePair(0, 0, distance=0.05, margin=0.4),
        PagePair(1, None),
        PagePair(2, 1, distance=0.42, margin=0.02),
        PagePair(None, 2),
    ))


class PagePlanTests(unittest.TestCase):
    def test_automatic_plan_highlights_uncertain_and_one_sided_rows(self):
        plan = PagePlan.from_match(automatic_match(), 3, 3)

        self.assertEqual([slot.kind for slot in plan.slots], [
            "matched", "source_only", "matched", "target_only",
        ])
        self.assertTrue(plan.slots[0].confirmed)
        self.assertFalse(plan.slots[1].confirmed)
        self.assertFalse(plan.slots[2].confirmed)
        self.assertFalse(plan.slots[3].confirmed)
        self.assertEqual(plan.as_dict()["unconfirmed_count"], 3)

    def test_moving_a_target_reorders_output_and_repairs_against_fixed_sources(self):
        plan = PagePlan.from_match(automatic_match(), 3, 3)

        moved, impact = plan.move_target(3, 1)

        self.assertEqual(
            [(slot.source_index, slot.target_index) for slot in moved.slots],
            [(0, 0), (1, 2), (2, None), (None, 1)],
        )
        self.assertEqual(impact.target_pages, (1, 2))
        self.assertEqual(impact.source_pages, (1, 2))
        self.assertGreaterEqual(impact.relationship_count, 2)
        self.assertFalse(moved.slots[1].confirmed)
        self.assertTrue(moved.slots[1].manual)
        self.assertEqual(
            [pair.target_index for pair in moved.to_match_result().pairs],
            [0, 2, None, 1],
        )

    def test_payload_accepts_confirmed_manual_reordering(self):
        payload = [
            {"source_index": 0, "target_index": 0, "confirmed": True},
            {"source_index": 1, "target_index": 2, "confirmed": True},
            {"source_index": 2, "target_index": None, "confirmed": False},
            {"source_index": None, "target_index": 1, "confirmed": True},
        ]

        plan = PagePlan.from_payload(3, 3, payload, automatic_match())

        self.assertEqual(plan.unconfirmed, (2,))
        self.assertTrue(plan.slots[1].manual)
        self.assertTrue(plan.slots[3].manual)

    def test_payload_rejects_missing_duplicate_empty_and_out_of_range_pages(self):
        invalid = (
            [
                {"source_index": 0, "target_index": 0, "confirmed": True},
                {"source_index": 0, "target_index": 1, "confirmed": True},
            ],
            [
                {"source_index": None, "target_index": None, "confirmed": True},
            ],
            [
                {"source_index": 0, "target_index": 3, "confirmed": True},
                {"source_index": 1, "target_index": 0, "confirmed": True},
                {"source_index": 2, "target_index": 1, "confirmed": True},
                {"source_index": None, "target_index": 2, "confirmed": True},
            ],
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(PagePlanError):
                PagePlan.from_payload(3, 3, payload, automatic_match())


if __name__ == "__main__":
    unittest.main()
