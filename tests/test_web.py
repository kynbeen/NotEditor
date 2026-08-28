from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from noteditor.web import SESSION_COOKIE, app, store
from tests.test_sdocx_transfer import make_pdf, make_sdocx


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.pdf = self.root / "source.pdf"
        make_pdf(self.pdf, ["ONE", "TWO"])
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.folder.cleanup()

    def test_health_ping_creates_no_session(self):
        client = TestClient(app)
        with client:
            before = len(store._sessions)
            for _ in range(5):
                response = client.get("/api/health")
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(SESSION_COOKIE, response.cookies)
            self.assertEqual(len(store._sessions), before)
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(len(store._sessions), before + 1)

    def test_serves_noteditor_ui_and_health(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["runtime"], "web")
        self.assertEqual(self.client.head("/api/health").status_code, 200)
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("<title>NotEditor</title>", page.text)

        manifest = self.client.get("/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.json()["display"], "standalone")
        self.assertEqual(self.client.get("/sw.js").status_code, 200)
        self.assertEqual(self.client.get("/icons/icon-192.png").status_code, 200)
        self.assertEqual(self.client.get("/icons/icon-512.png").status_code, 200)

    def test_upload_preview_and_export_pdf(self):
        response = self.client.post(
            "/api/documents",
            files=[("files", ("source.pdf", self.pdf.read_bytes(), "application/pdf"))],
        )
        self.assertEqual(response.status_code, 200, response.text)
        document = response.json()["added"][0]
        image = self.client.get(f"/api/documents/{document['id']}/pages/0?kind=thumbnail")
        self.assertTrue(image.json()["image"].startswith("data:image/png;base64,"))

        exported = self.client.post(
            "/api/documents/export",
            json={
                "order": [{"document_id": document["id"], "page_index": 1}],
                "suggested_name": "picked.pdf",
            },
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertTrue(exported.content.startswith(b"%PDF"))
        self.assertEqual(exported.headers["X-NotEditor-Page-Count"], "1")

    def test_upload_preview_and_export_handwriting(self):
        source_sdocx = self.root / "annotated.sdocx"
        make_sdocx(source_sdocx, self.pdf)

        source = self.client.post(
            "/api/handwriting/source",
            files={"file": ("annotated.sdocx", source_sdocx.read_bytes(), "application/zip")},
        )
        self.assertEqual(source.status_code, 200, source.text)
        self.assertFalse(source.json()["ready"])

        target = self.client.post(
            "/api/handwriting/target",
            files={"file": ("target.pdf", self.pdf.read_bytes(), "application/pdf")},
        )
        self.assertEqual(target.status_code, 200, target.text)
        self.assertTrue(target.json()["ready"])

        preview = self.client.get("/api/handwriting/preview?page_index=0&source_index=-2")
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.json()["ink"].startswith("data:image/png;base64,"))

        exported = self.client.post(
            "/api/handwriting/export",
            json={"suggested_name": "moved.sdocx", "target_mapping": None},
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertTrue(exported.content.startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()
