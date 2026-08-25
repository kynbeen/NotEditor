from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from pdf_page_composer.app import ComposerApi
from pdf_page_composer.engine import ComposerSession


class FakeWindow:
    def __init__(self, responses):
        self.responses = list(responses)

    def create_file_dialog(self, *_args, **_kwargs):
        return self.responses.pop(0)


class ComposerApiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.source = self.root / "source.pdf"
        document = pymupdf.open()
        document.new_page().insert_text((50, 50), "API TEST")
        document.save(self.source)
        document.close()
        self.api = ComposerApi(ComposerSession())
        self.webview = types.SimpleNamespace(FileDialog=types.SimpleNamespace(OPEN="open", SAVE="save"))

    def tearDown(self):
        self.api._close()
        self.folder.cleanup()

    def test_choose_parse_and_save_contract(self):
        output = self.root / "saved.pdf"
        self.api._bind_window(FakeWindow([(str(self.source),), str(output)]))
        with patch.dict(sys.modules, {"webview": self.webview}):
            chosen = self.api.choose_pdfs()
            self.assertTrue(chosen["ok"])
            source = chosen["added"][0]
            self.assertEqual(self.api.parse_range("1", 1), {"ok": True, "indices": [0]})
            saved = self.api.save_result([
                {"document_id": source["id"], "page_index": 0}
            ], "saved.pdf")
        self.assertTrue(saved["ok"])
        self.assertFalse(saved["cancelled"])
        self.assertTrue(output.exists())

    def test_cancelled_save_does_not_write(self):
        self.api._bind_window(FakeWindow([None]))
        with patch.dict(sys.modules, {"webview": self.webview}):
            result = self.api.save_result([], "nothing.pdf")
        self.assertEqual(result, {"ok": True, "cancelled": True})

    def test_health_contract_is_available_before_file_selection(self):
        result = self.api.health()
        self.assertTrue(result["ok"])
        self.assertRegex(result["version"], r"^\d+\.\d+\.\d+$")

    def test_native_objects_are_not_exposed_as_public_api_attributes(self):
        self.assertFalse(hasattr(self.api, "window"))
        self.assertFalse(hasattr(self.api, "session"))
        for name in dir(self.api):
            if name.startswith("_"):
                continue
            self.assertTrue(callable(getattr(self.api, name)), name)


if __name__ == "__main__":
    unittest.main()
