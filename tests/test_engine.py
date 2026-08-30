from __future__ import annotations

import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pikepdf
import pymupdf
from PIL import Image

import noteditor.engine as engine_module
from noteditor.engine import ComposerSession, EncryptedPdfError, PdfComposerError


def make_source(path: Path, labels: list[str], *, form: bool = False) -> None:
    document = pymupdf.open()
    sizes = [(595, 842), (720, 540), (612, 792)]
    for index, label in enumerate(labels):
        width, height = sizes[index % len(sizes)]
        page = document.new_page(width=width, height=height)
        page.insert_text((56, 72), label, fontsize=24)
        page.draw_rect(pymupdf.Rect(55, 95, 250, 155), color=(0.2, 0.3, 0.8), fill=(0.9, 0.92, 1))
        if index == 1:
            page.set_rotation(90)
        if index == 0:
            page.add_text_annot((300, 100), f"annotation-{label}")
            raster = BytesIO()
            Image.new("RGB", (24, 24), (42, 91, 180)).save(raster, format="PNG")
            page.insert_image(pymupdf.Rect(300, 145, 348, 193), stream=raster.getvalue())
        if form and index == 0:
            widget = pymupdf.Widget()
            widget.field_name = "shared-field"
            widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
            widget.field_value = label
            widget.rect = pymupdf.Rect(55, 190, 260, 220)
            page.add_widget(widget)
    if len(labels) > 1:
        document[0].insert_link({
            "kind": pymupdf.LINK_GOTO,
            "from": pymupdf.Rect(55, 240, 220, 270),
            "page": 1,
            "to": pymupdf.Point(0, 0),
        })
    document[0].insert_link({
        "kind": pymupdf.LINK_URI,
        "from": pymupdf.Rect(55, 285, 220, 315),
        "uri": "https://example.com/reference",
    })
    document.set_metadata({"title": f"Source {labels[0]}", "author": "PDF Page Composer Test"})
    document.save(path)
    document.close()


class ComposerEngineTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.session = ComposerSession()

    def tearDown(self):
        self.session.close()
        self.folder.cleanup()

    def test_combines_original_page_objects_in_requested_order(self):
        first_path = self.root / "first.pdf"
        second_path = self.root / "second.pdf"
        make_source(first_path, ["FIRST-1", "FIRST-2"], form=True)
        make_source(second_path, ["SECOND-1"], form=True)
        first, second = self.session.add_files([first_path, second_path])
        output = self.root / "combined.pdf"

        result = self.session.build_pdf([
            {"document_id": second["id"], "page_index": 0},
            {"document_id": first["id"], "page_index": 1},
            {"document_id": first["id"], "page_index": 0},
        ], output)

        self.assertEqual(result["page_count"], 3)
        self.assertGreater(result["size"], 0)
        with pymupdf.open(output) as combined:
            self.assertEqual([page.get_text().splitlines()[0] for page in combined], ["SECOND-1", "FIRST-2", "FIRST-1"])
            self.assertEqual(combined[1].rotation, 90)
            self.assertTrue(list(combined[2].annots()))
            self.assertTrue(combined[0].get_images(full=True))
            links = combined[2].get_links()
            internal = next(link for link in links if link["kind"] == pymupdf.LINK_GOTO)
            external = next(link for link in links if link["kind"] == pymupdf.LINK_URI)
            self.assertEqual(internal["page"], 1)
            self.assertEqual(external["uri"], "https://example.com/reference")
            widgets = [widget for page in combined for widget in (page.widgets() or [])]
            self.assertEqual(len(widgets), 2)
        with pikepdf.Pdf.open(output) as combined:
            self.assertIn("/AcroForm", combined.Root)
            self.assertEqual(str(combined.docinfo["/Producer"]), "PDF Page Composer (pikepdf/qpdf)")

    def test_duplicate_page_is_rejected(self):
        path = self.root / "one.pdf"
        make_source(path, ["ONLY"])
        source = self.session.add_files([path])[0]
        ref = {"document_id": source["id"], "page_index": 0}
        with self.assertRaises(PdfComposerError):
            self.session.build_pdf([ref, ref], self.root / "bad.pdf")

    def test_encrypted_pdf_is_rejected(self):
        plain = self.root / "plain.pdf"
        encrypted = self.root / "encrypted.pdf"
        make_source(plain, ["SECRET"])
        with pikepdf.Pdf.open(plain) as source:
            source.save(encrypted, encryption=pikepdf.Encryption(owner="owner", user="user"))
        with self.assertRaises(EncryptedPdfError):
            self.session.add_files([encrypted])

    def test_preview_is_png_data_uri_and_cached(self):
        path = self.root / "preview.pdf"
        make_source(path, ["PREVIEW"])
        source = self.session.add_files([path])[0]
        first = self.session.page_image(source["id"], 0, "thumbnail")
        second = self.session.page_image(source["id"], 0, "thumbnail")
        self.assertTrue(first.startswith("data:image/png;base64,"))
        self.assertIs(first, second)

    def test_concurrent_duplicate_preview_requests_share_one_render(self):
        path = self.root / "shared.pdf"
        make_source(path, ["SHARED"])
        source = self.session.add_files([path])[0]
        original = self.session._render_page_image
        calls = 0
        calls_lock = threading.Lock()

        def slow_render(*args):
            nonlocal calls
            with calls_lock:
                calls += 1
            time.sleep(0.08)
            return original(*args)

        with patch.object(self.session, "_render_page_image", side_effect=slow_render):
            with ThreadPoolExecutor(max_workers=8) as pool:
                values = list(pool.map(
                    lambda _index: self.session.page_image(source["id"], 0, "thumbnail"),
                    range(8),
                ))

        self.assertEqual(calls, 1)
        self.assertEqual(len(set(values)), 1)

    def test_preview_cache_evicts_old_entries_at_the_byte_limit(self):
        path = self.root / "bounded.pdf"
        make_source(path, ["ONE", "TWO", "THREE"])
        bounded = ComposerSession(preview_cache_max_bytes=30)
        self.addCleanup(bounded.close)
        source = bounded.add_files([path])[0]
        calls: list[int] = []

        def fake_render(_source, page_index, _kind):
            calls.append(page_index)
            return f"data:image/png;base64,{page_index:04d}"

        with patch.object(bounded, "_render_page_image", side_effect=fake_render):
            bounded.page_image(source["id"], 0, "thumbnail")
            bounded.page_image(source["id"], 1, "thumbnail")
            bounded.page_image(source["id"], 0, "thumbnail")

        self.assertEqual(calls, [0, 1, 0])
        self.assertLessEqual(bounded._preview_cache_bytes, 30)
        self.assertEqual(len(bounded._preview_cache), 1)

    def test_preview_rendering_respects_the_process_wide_concurrency_limit(self):
        path = self.root / "many.pdf"
        make_source(path, [f"PAGE-{index}" for index in range(8)])
        source = self.session.add_files([path])[0]
        active = 0
        maximum = 0
        active_lock = threading.Lock()

        def counted_render(_source, page_index, _kind):
            nonlocal active, maximum
            with active_lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.05)
            with active_lock:
                active -= 1
            return f"data:image/png;base64,{page_index}"

        with (
            patch.object(engine_module, "_PREVIEW_RENDER_SLOTS", threading.BoundedSemaphore(2)),
            patch.object(self.session, "_render_page_image", side_effect=counted_render),
            ThreadPoolExecutor(max_workers=8) as pool,
        ):
            values = list(pool.map(
                lambda index: self.session.page_image(source["id"], index, "thumbnail"),
                range(8),
            ))

        self.assertEqual(len(values), 8)
        self.assertEqual(maximum, 2)


if __name__ == "__main__":
    unittest.main()
