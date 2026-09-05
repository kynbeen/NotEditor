from __future__ import annotations

import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf


class FakeRawDocument:
    def __init__(self, page_count: int):
        self._page_count = page_count

    def countPages(self) -> int:
        return self._page_count

    def needsPassword(self) -> bool:
        return False

    def destroy(self) -> None:
        pass


class AndroidPdfAdapterTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.source = self.root / "source.pdf"
        with pymupdf.open() as document:
            first = document.new_page(width=200, height=100)
            first.insert_text((20, 55), "FIRST", fontsize=24)
            second = document.new_page(width=120, height=180)
            second.insert_text((20, 60), "SECOND", fontsize=20)
            document.save(self.source)

        fake_java = types.ModuleType("java")
        fake_java.jclass = lambda _name: type("UnusedJavaClass", (), {})
        self.java_patch = patch.dict(sys.modules, {"java": fake_java})
        self.java_patch.start()
        sys.modules.pop("noteditor._android_pdf", None)
        self.adapter = importlib.import_module("noteditor._android_pdf")

    def tearDown(self):
        sys.modules.pop("noteditor._android_pdf", None)
        self.java_patch.stop()
        self.folder.cleanup()

    def test_adapter_writes_copied_blank_and_transformed_pages(self):
        source = self.adapter.Document(FakeRawDocument(2), source=self.source)
        output_path = self.root / "output.pdf"
        with self.adapter.open() as output:
            output.insert_pdf(source, from_page=1, to_page=1)
            page = output.new_page(width=300, height=300)
            page.show_pdf_page(self.adapter.Rect(50, 50, 250, 250), source, 0)
            output.save(output_path)

        with pymupdf.open(output_path) as result:
            self.assertEqual(result.page_count, 2)
            self.assertEqual(result[0].get_text().strip(), "SECOND")
            self.assertEqual((result[1].rect.width, result[1].rect.height), (300, 300))
            self.assertEqual(result[1].get_text().strip(), "FIRST")

    def test_geometry_helpers_match_the_pymupdf_subset(self):
        rect = self.adapter.Rect(10, 20, 110, 220)
        self.assertEqual((rect.width, rect.height), (100, 200))
        self.assertFalse(rect.is_empty)
        self.assertFalse(rect.is_infinite)

        matrix = self.adapter.Matrix(2, 3)
        self.assertEqual((matrix.a, matrix.b, matrix.c, matrix.d), (2, 0, 0, 3))


if __name__ == "__main__":
    unittest.main()
