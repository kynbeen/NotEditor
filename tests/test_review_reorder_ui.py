"""Run the production matching controls in Node, including confirmation outcomes."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


APP_JS = Path(__file__).parents[1] / "noteditor" / "static" / "app.js"
DRIVER = r"""
const fs = require("node:fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const scenario = JSON.parse(process.argv[2]);
function grab(name) {
  const pattern = new RegExp("^(?:async )?function " + name + "\\(", "m");
  const start = src.search(pattern);
  if (start < 0) throw new Error("Missing function " + name);
  const rest = src.slice(start);
  const next = rest.slice(1).search(/^(?:async )?function /m);
  return next < 0 ? rest : rest.slice(0, next + 1);
}
const state = { handwriting: { plan: scenario.plan } };
let renders = 0, prompts = 0;
const confirmReviewReorder = async () => {
  prompts += 1;
  if (scenario.replacePlan) state.handwriting.plan = [];
  return scenario.accept !== false;
};
const body = ["describeChangedPages", "shiftedTargetPlan", "reviewMoveDestination",
  "moveReviewTarget"].map(grab).join("\n");
const api = new Function("state", "confirmReviewReorder", "renderPageReview",
  body + "\nreturn { moveReviewTarget, reviewMoveDestination };")(
    state, confirmReviewReorder, () => { renders += 1; });
(async () => {
  const to = scenario.direction
    ? api.reviewMoveDestination(scenario.from, scenario.direction) : scenario.to;
  await api.moveReviewTarget(scenario.from, to);
  process.stdout.write(JSON.stringify({ plan: state.handwriting.plan, renders, prompts }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""


def slot(source, target, *, excluded=False):
    return dict(source_index=source, target_index=target, confirmed=True,
                manual=False, attention=False, excluded=excluded)


def run_move(plan, **options):
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("Node is required to execute app.js")
    result = subprocess.run(
        [node, "-e", DRIVER, str(APP_JS), json.dumps(dict(plan=plan, **options))],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


class ReviewReorderTests(unittest.TestCase):
    def test_native_pages_follow_retained_neighbors_including_other_native_pages(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required to execute app.js")
        source_order = [dict(page_id="a", source_index=0, blank=False),
                        dict(page_id="b", source_index=1, blank=True),
                        dict(page_id="n1", source_index=None, blank=False),
                        dict(page_id="n2", source_index=None, blank=False),
                        dict(page_id="c", source_index=2, blank=False)]
        cases = [
            ([slot(0, 0), slot(1, None), slot(2, 1)], ["a", "n1", "n2", "b", "c"]),
            ([slot(2, 0), slot(0, 1)], ["c", "a", "n1", "n2"]),
            ([slot(0, 0, excluded=True), slot(2, 1)], ["a", "n1", "n2", "c"]),
            ([slot(None, 0)], ["n1", "n2", None]),
        ]
        driver = DRIVER.split("const state =", 1)[0] + r'''
const place = new Function(grab("nativeReviewPlacement") + "\nreturn nativeReviewPlacement;")();
process.stdout.write(JSON.stringify(place(scenario.plan, scenario.sourceOrder)
  .map(entry => entry.page?.page_id || null)));
'''
        for plan, expected in cases:
            with self.subTest(plan=plan):
                result = subprocess.run([node, "-e", driver, str(APP_JS),
                    json.dumps(dict(plan=plan, sourceOrder=source_order))],
                    capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(result.stdout), expected)

    def test_move_changes_targets_and_requires_reconfirmation(self):
        before = [slot(i, i) for i in range(3)]
        result = run_move(before, **{"from": 0, "direction": 1})
        self.assertEqual([s["target_index"] for s in result["plan"]], [1, 0, 2])
        self.assertEqual([s["source_index"] for s in result["plan"]], [0, 1, 2])
        self.assertFalse(result["plan"][0]["confirmed"])
        self.assertTrue(result["plan"][0]["manual"])
        self.assertEqual(result["plan"][2], before[2])
        self.assertEqual((result["prompts"], result["renders"]), (1, 1))

    def test_excluded_pair_stays_excluded_and_does_not_shift(self):
        before = [slot(0, 0), slot(1, 1, excluded=True), slot(2, 2)]
        result = run_move(before, **{"from": 0, "direction": 1})
        self.assertEqual([s["target_index"] for s in result["plan"]], [2, 1, 0])
        self.assertEqual(result["plan"][1], before[1])

    def test_cancel_preserves_every_slot(self):
        before = [slot(0, 0), slot(1, 1)]
        result = run_move(before, **{"from": 1, "to": 0, "accept": False})
        self.assertEqual(result["plan"], before)
        self.assertEqual(result["renders"], 0)

    def test_boundary_and_excluded_rows_cannot_be_moved(self):
        before = [slot(0, 0), slot(1, 1, excluded=True), slot(2, 2)]
        for options in ({"from": 0, "direction": -1}, {"from": 2, "direction": 1},
                        {"from": 1, "to": 0}, {"from": 0, "to": 1}):
            with self.subTest(options=options):
                result = run_move(before, **options)
                self.assertEqual(result["plan"], before)
                self.assertEqual(result["prompts"], 0)

    def test_missing_target_cannot_be_dragged_but_can_receive_target(self):
        before = [slot(0, None), slot(1, 0)]
        self.assertEqual(run_move(before, **{"from": 0, "to": 1})["plan"], before)
        result = run_move(before, **{"from": 1, "to": 0})
        self.assertEqual([s["target_index"] for s in result["plan"]], [0, None])

    def test_new_target_can_be_matched_without_leaving_empty_rows(self):
        result = run_move([slot(0, None), slot(None, 0)], **{"from": 1, "to": 0})
        self.assertEqual(len(result["plan"]), 1)
        self.assertEqual((result["plan"][0]["source_index"], result["plan"][0]["target_index"]), (0, 0))

    def test_confirmation_cannot_overwrite_a_replaced_plan(self):
        result = run_move([slot(0, 0), slot(1, 1)],
                          **{"from": 0, "to": 1, "replacePlan": True})
        self.assertEqual(result["plan"], [])
        self.assertEqual(result["renders"], 0)


if __name__ == "__main__":
    unittest.main()
