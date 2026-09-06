"""Execute the real JavaScript order functions against selection and reorder scenarios."""
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
function grab(name) {
  const start = src.indexOf("function " + name + "(");
  if (start < 0) throw new Error("missing function: " + name);
  let depth = 0, i = src.indexOf("{", start);
  for (let j = i; j < src.length; j += 1) {
    if (src[j] === "{") depth += 1;
    else if (src[j] === "}") { depth -= 1; if (!depth) return src.slice(start, j + 1); }
  }
  throw new Error("unterminated function: " + name);
}
const state = { documents: [], selected: new Set(), order: [], orderDirty: false };
const pageKey = (id, i) => `${id}:${i}`;
const refKey = (r) => `${r.document_id}:${r.page_index}`;
const body = ["defaultOrder", "syncOrder", "insertNearOwnPages"].map(grab).join("\n");
const api = new Function("state", "pageKey", "refKey",
  body + "\nreturn { syncOrder };")(state, pageKey, refKey);

const scenario = JSON.parse(process.argv[2]);
state.documents = scenario.documents.map((doc) => ({
  id: doc.id, pages: Array.from({ length: doc.page_count }, (_, i) => ({ index: i })),
}));
for (const step of scenario.steps) {
  if (step.action === "move") {
    const [moved] = state.order.splice(step.from, 1);
    state.order.splice(step.to, 0, moved);
    state.orderDirty = true;
    continue;
  }
  const doc = state.documents.find((d) => d.id === step.document);
  if (step.action === "select_all") {
    doc.pages.forEach((p) => state.selected.add(pageKey(doc.id, p.index)));
  } else if (step.action === "toggle") {
    const key = pageKey(doc.id, step.page);
    if (state.selected.has(key)) state.selected.delete(key); else state.selected.add(key);
  } else {
    doc.pages.forEach((p) => state.selected.delete(pageKey(doc.id, p.index)));
    for (let i = step.first; i <= step.last; i += 1) state.selected.add(pageKey(doc.id, i));
  }
  api.syncOrder();
}
process.stdout.write(JSON.stringify(state.order));
"""


def run_scenario(scenario: dict) -> list[str]:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("Node is required to execute app.js")
    done = subprocess.run(
        [node, "-e", DRIVER, str(APP_JS), json.dumps(scenario)],
        capture_output=True,
        text=True,
        check=True,
    )
    return [f"{item['document_id']}:{item['page_index']}" for item in json.loads(done.stdout)]


class OrderSyncTests(unittest.TestCase):
    def test_default_order_follows_documents_and_page_numbers(self):
        order = run_scenario({
            "documents": [{"id": "A", "page_count": 3}, {"id": "B", "page_count": 2}],
            "steps": [
                {"action": "select_all", "document": "A"},
                {"action": "select_all", "document": "B"},
            ],
        })
        self.assertEqual(order, ["A:0", "A:1", "A:2", "B:0", "B:1"])

    def test_manual_order_survives_later_selection_changes(self):
        order = run_scenario({
            "documents": [{"id": "A", "page_count": 3}, {"id": "B", "page_count": 2}],
            "steps": [
                {"action": "select_all", "document": "A"},
                {"action": "select_all", "document": "B"},
                {"action": "move", "from": 4, "to": 0},
                {"action": "toggle", "document": "A", "page": 1},
            ],
        })
        self.assertEqual(order, ["B:1", "A:0", "A:2", "B:0"])

    def test_new_page_is_inserted_beside_its_document_after_reorder(self):
        order = run_scenario({
            "documents": [{"id": "A", "page_count": 4}, {"id": "B", "page_count": 2}],
            "steps": [
                {"action": "range", "document": "A", "first": 1, "last": 2},
                {"action": "select_all", "document": "B"},
                {"action": "move", "from": 3, "to": 0},
                {"action": "toggle", "document": "A", "page": 0},
            ],
        })
        self.assertEqual(order, ["B:1", "A:0", "A:1", "A:2", "B:0"])


if __name__ == "__main__":
    unittest.main()
