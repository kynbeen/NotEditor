"""Unmatched annotated pages remain visible and saved at the inferred position."""
from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

import pymupdf
from PIL import Image

from noteditor.app import ComposerApi
from noteditor.goodnotes_archive import background_pdf, read_document, safe_members
from noteditor.handwriting_transfer import inspect_transfer, preview_transfer, transfer_handwriting
from noteditor.page_plan import PagePlan, PlanSlot
from tests.test_goodnotes_transfer import FIXTURE
from tests.test_notewise_transfer import _make_notewise
from tests.test_page_match import make_document, SEEDS
from tests.test_sdocx_ink import make_stroke_layers
from tests.test_sdocx_rebuild import make_rebuild_source, UUIDS
from tests.test_sdocx_page import make_page
from noteditor.sdocx_note import PageOrder, PageOrderEntry, read_page_order
from noteditor.sdocx_page import page_hash
from noteditor.sdocx_transfer import _rewrite_archive, preview_native_page, SdocxTransferError


class PreservedSourcePageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.original_pdf = make_document(self.root / "original.pdf", SEEDS[:4])
        self.target = self.root / "target.pdf"
        with pymupdf.open(self.original_pdf) as original, pymupdf.open() as target:
            for index in (0, 1, 3):
                target.insert_pdf(original, from_page=index, to_page=index)
            target.save(self.target)

    def make_source(self, suffix):
        source = self.root / f"source{suffix}"
        if suffix == ".sdocx":
            make_rebuild_source(source, self.original_pdf, annotated_layers=make_stroke_layers())
        elif suffix == ".notewise":
            _make_notewise(source, self.original_pdf, pages=4, canvas=(640, 480))
        else:
            transfer_handwriting(FIXTURE, self.original_pdf, source, plan_override=PagePlan(1, 4, (
                PlanSlot(None, 0, True), PlanSlot(None, 1, True),
                PlanSlot(0, 2, True), PlanSlot(None, 3, True),
            )))
        return source

    def read_background(self, source):
        with ZipFile(source) as archive:
            if source.suffix == ".goodnotes":
                return background_pdf(archive, read_document(archive, safe_members(archive)))
            if source.suffix == ".sdocx":
                name = next(n for n in archive.namelist() if n.endswith(".pdf"))
            else:
                name = next(n for n in archive.namelist() if n.startswith("pdf/"))
            return archive.read(name)

    def test_unmatched_ink_is_previewed_and_saved_between_matched_neighbors(self):
        for suffix in (".sdocx", ".notewise", ".goodnotes"):
            with self.subTest(format=suffix):
                source = self.make_source(suffix)
                original_bytes = source.read_bytes()
                inspection = inspect_transfer(source, self.target)
                pairs = [(p.source_index, p.target_index) for p in inspection.match.pairs]
                self.assertEqual(pairs, [(0, 0), (1, 1), (2, None), (3, 2)])
                before, after, ink, count = preview_transfer(
                    source, self.target, -1, inspection, source_index_override=2
                )
                self.assertEqual(before, after)
                self.assertGreater(count, 0)
                with Image.open(io.BytesIO(ink)) as layer:
                    self.assertIsNotNone(layer.getchannel("A").getbbox())
                with pymupdf.open(stream=self.read_background(source), filetype="pdf") as original:
                    page = original[2]
                    scale = min(900 / max(page.rect.width, page.rect.height), 3)
                    self.assertEqual(after, page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                                                          alpha=False).tobytes("png"))
                output = self.root / f"preserved{suffix}"
                rows = [dict(source_index=s, target_index=t, confirmed=True) for s, t in pairs]
                plan = PagePlan.from_payload(4, 3, rows, inspection.match)
                transfer_handwriting(source, self.target, output, plan_override=plan)
                _before, preserved, preserved_ink, preserved_count = preview_transfer(
                    output, self.target, -1, source_index_override=2
                )
                self.assertEqual(preserved, after)
                self.assertEqual(preserved_ink, ink)
                self.assertEqual(preserved_count, count)
                with pymupdf.open(stream=self.read_background(output), filetype="pdf") as result, \
                     pymupdf.open(self.original_pdf) as original:
                    self.assertEqual(result.page_count, 4)
                    self.assertEqual([p.get_pixmap().samples for p in result],
                                     [p.get_pixmap().samples for p in original])
                self.assertEqual(source.read_bytes(), original_bytes)

    def test_desktop_android_dispatch_preserves_source_only_preview_request(self):
        source = self.make_source(".sdocx")
        api = ComposerApi()
        self.addCleanup(api._close, True)
        api._set_handwriting_path("source", source)
        api._set_handwriting_path("target", self.target)
        api._handwriting_future.result(timeout=5)
        response = json.loads(api.dispatch_call("handwriting_preview", "[-1, 2]"))
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["index"], -1)
        self.assertEqual(response["before"], response["after"])
        self.assertGreater(response["stroke_count"], 0)
        self.assertTrue(base64.b64decode(response["after"].split(",", 1)[1]).startswith(b"\x89PNG"))
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from noteditor.web import app
        with patch("noteditor.web._api", return_value=api), TestClient(app) as client:
            web = client.get("/api/handwriting/preview?page_index=-1&source_index=2")
        self.assertEqual(web.status_code, 200, web.text)
        self.assertEqual(web.json()["after"], response["after"])

    def test_native_note_is_previewed_and_preserved_in_notebook_order(self):
        source = self.make_source(".sdocx")
        native = make_page(uuid=UUIDS[4], mask=0x60, canvas=(640, 480),
                           strokes=make_stroke_layers(), hash_block=b"N" * 32)
        with ZipFile(source) as archive:
            order = read_page_order(archive.read("pageIdInfo.dat"))
        entries = list(order.entries[:-1])
        entries.insert(2, PageOrderEntry(UUIDS[4], page_hash(native)))
        edited = self.root / "native-middle.sdocx"
        _rewrite_archive(source, edited, {
            f"{UUIDS[4]}.page": native,
            "pageIdInfo.dat": PageOrder(order.file_hash, tuple(entries)).to_bytes(),
        })
        inspection = inspect_transfer(edited, self.target)
        metadata = inspection.as_dict()["source_order"]
        self.assertEqual([p["source_index"] for p in metadata], [0, 1, None, 2, 3])
        self.assertEqual(metadata[2]["page_number"], 3)
        self.assertFalse(metadata[2]["blank"])
        before, after, ink, count = preview_native_page(edited, UUIDS[4])
        self.assertEqual(before, after)
        self.assertGreater(count, 0)
        with Image.open(io.BytesIO(after)) as background, Image.open(io.BytesIO(ink)) as layer:
            self.assertEqual(background.getpixel((0, 0)), (252, 252, 252))
            self.assertEqual(background.size, layer.size)
            self.assertIsNotNone(layer.getchannel("A").getbbox())
        api = ComposerApi()
        self.addCleanup(api._close, True)
        api._set_handwriting_path("source", edited)
        api._set_handwriting_path("target", self.target)
        api._handwriting_future.result(timeout=5)
        response = json.loads(api.dispatch_call("handwriting_preview", json.dumps([-1, -1, UUIDS[4]])))
        self.assertTrue(response["ok"], response)
        self.assertEqual(base64.b64decode(response["after"].split(",", 1)[1]), after)
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from noteditor.web import app
        with patch("noteditor.web._api", return_value=api), TestClient(app) as client:
            web = client.get("/api/handwriting/preview", params={"native_page_id": UUIDS[4]})
        self.assertEqual(web.json()["after"], response["after"])
        output = self.root / "native-result.sdocx"
        plan = PagePlan.from_payload(4, 3, [
            dict(source_index=p.source_index, target_index=p.target_index, confirmed=True)
            for p in inspection.match.pairs
        ], inspection.match)
        transfer_handwriting(edited, self.target, output, plan_override=plan)
        with ZipFile(output) as archive:
            saved = read_page_order(archive.read("pageIdInfo.dat"))
            self.assertEqual([entry.uuid for entry in saved.entries],
                             [UUIDS[0], UUIDS[1], UUIDS[4], UUIDS[2], UUIDS[3]])
            self.assertEqual(archive.read(f"{UUIDS[4]}.page"), native)
        self.assertEqual(preview_native_page(output, UUIDS[4]), (before, after, ink, count))

    def test_native_preview_rejects_pdf_page_and_unknown_id(self):
        source = self.make_source(".sdocx")
        for page_id in (UUIDS[0], "../note", "unknown"):
            with self.subTest(page_id=page_id), self.assertRaises(SdocxTransferError):
                preview_native_page(source, page_id)
