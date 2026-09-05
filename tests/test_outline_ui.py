"""Exercise outline parsing and pending file-read invalidation in the real UI code."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

APP_JS = Path(__file__).parents[1] / "noteditor" / "static" / "app.js"
DRIVER = r"""
const fs = require('node:fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const scenario = JSON.parse(process.argv[2]);
const body = src.slice(src.indexOf('function clearOutlineJson('),
  src.indexOf('async function saveHandwritingTransfer('));
const state = {outlineRevision: 0};
const refs = {outlineJsonText: {value: 'previous'}, outlineJsonStatus: {textContent: ''},
  outlineJsonInput: {value: '', files: []}};
const api = new Function('state', 'refs', body +
  '\nreturn {parseOutlineJson, loadOutlineJson, clearOutlineJson};')(state, refs);
(async () => {
  if (scenario.mode === 'parse') {
    try { return {entries: api.parseOutlineJson(scenario.text)}; }
    catch (error) { return {error: error.message}; }
  }
  let finish;
  refs.outlineJsonInput.files = [{name: 'outline.json', text: () => new Promise(r => {finish = r;})}];
  const pending = api.loadOutlineJson();
  if (scenario.mode === 'edit') {
    refs.outlineJsonText.value = 'user edit'; state.outlineRevision += 1;
  } else if (scenario.mode === 'clear') api.clearOutlineJson();
  finish(scenario.text);
  await pending;
  return {text: refs.outlineJsonText.value, status: refs.outlineJsonStatus.textContent};
})().then(result => process.stdout.write(JSON.stringify(result)));
"""


def run(**scenario):
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("Node is required to execute app.js")
    result = subprocess.run([node, "-e", DRIVER, str(APP_JS), json.dumps(scenario)],
                            capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(result.stdout)


class OutlineUiTests(unittest.TestCase):
    def test_parser_accepts_blank_and_preserves_titles(self):
        self.assertIsNone(run(mode="parse", text="  ")["entries"])
        entries = [{"page": 2, "title": "  Title  "}]
        self.assertEqual(run(mode="parse", text=json.dumps(entries))["entries"], entries)

    def test_parser_rejects_invalid_entries(self):
        for data in (None, [], {}, [None], [{"page": True, "title": "A"}],
                     [{"page": 0, "title": "A"}], [{"page": 1, "title": " "}],
                     [{"page": 1, "title": "A", "extra": True}]):
            with self.subTest(data=data):
                self.assertIn("error", run(mode="parse", text=json.dumps(data)))

    def test_late_file_read_does_not_replace_manual_edits_or_clear(self):
        text = '[{"page":1,"title":"file"}]'
        self.assertEqual(run(mode="edit", text=text)["text"], "user edit")
        self.assertEqual(run(mode="clear", text=text)["text"], "")
        self.assertEqual(run(mode="file", text=text)["text"], text)

    def test_invalid_file_preserves_existing_editor_content(self):
        result = run(mode="file", text="not json")
        self.assertEqual(result["text"], "previous")
        self.assertTrue(result["status"])
