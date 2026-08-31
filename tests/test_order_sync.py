"""결과 순서가 합치기 인계 규격으로 **기록 가능한 상태를 유지하는가**.

규격은 문서 하나당 한 구간, 구간 안은 오름차순만 표현할 수 있다(`parts_from_order`).
그래서 화면에서 쪽을 고르는 것만으로 `A…B…A` 가 되어 버리면, 사용자는 아무 잘못도 하지
않았는데 저장이 막힌다 — 실제로 났던 사고다:

    1주차(1) 30-50 쪽과 1주차(2-1) 1-16 쪽을 고른 뒤, 1주차(1) 범위를 다시 넓혔더니
    "현재 결과 순서는 합치기 인계 규격에 손실 없이 기록할 수 없습니다" 로 막혔다.

순서 계산은 `app.js` 안에만 있어 파이썬으로 옮겨 적으면 진짜 코드를 검사하지 못한다.
그래서 그 파일에서 함수를 그대로 잘라 Node 로 돌린다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

APP_JS = Path(__file__).parents[1] / "noteditor" / "static" / "app.js"

# app.js 의 순서 계산 함수만 그대로 잘라 와 시나리오를 돌리고 결과 순서를 JSON 으로 뱉는다.
DRIVER = r"""
const fs = require("node:fs");
const src = fs.readFileSync(process.argv[1], "utf8");
function grab(name) {
  const start = src.indexOf("function " + name + "(");
  if (start < 0) throw new Error("함수를 찾지 못했습니다: " + name);
  let depth = 0, i = src.indexOf("{", start);
  for (let j = i; j < src.length; j += 1) {
    if (src[j] === "{") depth += 1;
    else if (src[j] === "}") { depth -= 1; if (!depth) return src.slice(start, j + 1); }
  }
  throw new Error("함수 끝을 찾지 못했습니다: " + name);
}
const state = { documents: [], selected: new Set(), order: [], orderDirty: false };
const pageKey = (id, i) => `${id}:${i}`;
const refKey = (r) => `${r.document_id}:${r.page_index}`;
const body = ["defaultOrder", "syncOrder", "insertNearOwnBlock"].map(grab).join("\n");
const api = new Function("state", "pageKey", "refKey",
  body + "\nreturn { defaultOrder, syncOrder };")(state, pageKey, refKey);

const scenario = JSON.parse(process.argv[2]);
state.documents = scenario.documents.map((doc) => ({
  id: doc.id, pages: Array.from({ length: doc.page_count }, (_, i) => ({ index: i })),
}));
state.orderDirty = !!scenario.order_dirty;
for (const step of scenario.steps) {
  const doc = state.documents.find((d) => d.id === step.document);
  if (step.action === "select_all") {
    doc.pages.forEach((p) => state.selected.add(pageKey(doc.id, p.index)));
  } else {
    doc.pages.forEach((p) => state.selected.delete(pageKey(doc.id, p.index)));
    for (let i = step.first; i <= step.last; i += 1) state.selected.add(pageKey(doc.id, i));
  }
  api.syncOrder();
}
process.stdout.write(JSON.stringify(state.order));
"""


def run_scenario(scenario: dict) -> list[dict]:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("Node 가 없어 app.js 순서 계산을 실행할 수 없습니다")
    done = subprocess.run(
        [node, "-e", DRIVER, str(APP_JS), json.dumps(scenario)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(done.stdout)


def blocks_of(order: list[dict]) -> list[tuple[str, list[int]]]:
    blocks: list[tuple[str, list[int]]] = []
    for ref in order:
        if blocks and blocks[-1][0] == ref["document_id"]:
            blocks[-1][1].append(ref["page_index"])
        else:
            blocks.append((ref["document_id"], [ref["page_index"]]))
    return blocks


class OrderStaysRecordableTests(unittest.TestCase):
    def assert_recordable(self, order: list[dict]) -> list[tuple[str, list[int]]]:
        blocks = blocks_of(order)
        names = [name for name, _ in blocks]
        self.assertEqual(len(set(names)), len(names),
                         f"문서가 여러 구간으로 쪼개졌습니다: {names}")
        for name, pages in blocks:
            self.assertEqual(pages, sorted(set(pages)), f"{name} 구간이 오름차순이 아닙니다")
        return blocks

    def test_widening_an_earlier_range_keeps_that_document_in_one_block(self):
        """실제 사고 시나리오. 넓힌 쪽이 끝에 붙으면 `A…B…A` 가 되어 저장이 막혔다."""
        order = run_scenario({
            "documents": [{"id": "A", "page_count": 60}, {"id": "B", "page_count": 20}],
            "order_dirty": True,       # 사용자가 순서를 손댄 뒤여도 규격은 지켜져야 한다
            "steps": [
                {"action": "select_all", "document": "A"},
                {"action": "select_all", "document": "B"},
                {"action": "range", "document": "A", "first": 29, "last": 49},
                {"action": "range", "document": "B", "first": 0, "last": 15},
                {"action": "range", "document": "A", "first": 29, "last": 50},
            ],
        })
        blocks = self.assert_recordable(order)
        self.assertEqual([name for name, _ in blocks], ["A", "B"])
        self.assertEqual(blocks[0][1], list(range(29, 51)))
        self.assertEqual(blocks[1][1], list(range(0, 16)))

    def test_a_page_added_below_the_current_range_lands_in_page_order(self):
        """구간 안 오름차순도 규격이다 — 뒤에 끼우면 내림차순이 되어 역시 막힌다."""
        order = run_scenario({
            "documents": [{"id": "A", "page_count": 30}, {"id": "B", "page_count": 10}],
            "order_dirty": True,
            "steps": [
                {"action": "range", "document": "A", "first": 10, "last": 12},
                {"action": "range", "document": "B", "first": 0, "last": 2},
                {"action": "range", "document": "A", "first": 4, "last": 12},
            ],
        })
        blocks = self.assert_recordable(order)
        self.assertEqual(blocks[0], ("A", list(range(4, 13))))


if __name__ == "__main__":
    unittest.main()
