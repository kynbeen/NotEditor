from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
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

    def test_handwriting_transfer_file_dialog_flow(self):
        source = self.root / "annotated.sdocx"
        target = self.root / "target.pdf"
        output = self.root / "target-필기.sdocx"
        source.write_bytes(b"zip")
        target.write_bytes(b"%PDF")
        inspection = SimpleNamespace(as_dict=lambda: {
            "source_name": source.name,
            "target_name": target.name,
            "page_count": 2,
            "annotated_page_count": 1,
            "stroke_cache_count": 1,
        })
        self.api._bind_window(FakeWindow([(str(source),), (str(target),), str(output)]))
        with patch.dict(sys.modules, {"webview": self.webview}), \
                patch("pdf_page_composer.app.inspect_transfer", return_value=inspection), \
                patch("pdf_page_composer.app.transfer_handwriting", return_value={
                    "path": str(output), "page_count": 2,
                }) as transfer:
            selected_source = self.api.choose_handwriting_source()
            selected_target = self.api.choose_handwriting_target()
            saved = self.api.save_handwriting_transfer("target-필기.sdocx")

        self.assertTrue(selected_source["ok"])
        self.assertFalse(selected_source["ready"])
        self.assertTrue(selected_target["ready"])
        self.assertTrue(saved["ok"])
        self.assertFalse(saved["cancelled"])
        transfer.assert_called_once_with(source.resolve(), target.resolve(), output.resolve())

    def test_handwriting_preview_returns_two_png_data_uris(self):
        source = self.root / "annotated.sdocx"
        target = self.root / "target.pdf"
        source.write_bytes(b"zip")
        target.write_bytes(b"%PDF")
        inspection = SimpleNamespace(page_count=3, alignment=None, as_dict=lambda: {"page_count": 3})
        self.api._handwriting_source = source
        self.api._handwriting_target = target
        with patch("pdf_page_composer.app.inspect_transfer", return_value=inspection) as inspect, \
                patch("pdf_page_composer.app.preview_transfer",
                      return_value=(b"\x89PNG-before", b"\x89PNG-after")) as preview:
            first = self.api.handwriting_preview(1)
            second = self.api.handwriting_preview(99)

        self.assertTrue(first["ok"])
        self.assertEqual(first["index"], 1)
        self.assertEqual(first["page_count"], 3)
        self.assertTrue(first["before"].startswith("data:image/png;base64,"))
        self.assertTrue(first["after"].startswith("data:image/png;base64,"))
        # 범위를 벗어난 쪽 번호는 마지막 쪽으로 좁힌다.
        self.assertEqual(second["index"], 2)
        # 파일이 그대로면 검사 결과를 다시 계산하지 않는다.
        inspect.assert_called_once()
        self.assertEqual(preview.call_count, 2)


if __name__ == "__main__":
    unittest.main()
