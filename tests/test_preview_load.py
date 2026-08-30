from __future__ import annotations

import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import noteditor.engine as engine_module
from noteditor.engine import ComposerSession
from noteditor.web import app
from tests.test_sdocx_transfer import make_pdf


class PreviewLoadTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.session = ComposerSession(preview_cache_max_bytes=256)

    def tearDown(self):
        self.session.close()
        self.folder.cleanup()

    def test_three_hundred_pages_keep_rendering_bounded_and_health_fast(self):
        paths = []
        for document_index in range(3):
            path = self.root / f"document-{document_index + 1}.pdf"
            make_pdf(
                path,
                [f"DOCUMENT {document_index + 1} PAGE {page + 1}" for page in range(100)],
            )
            paths.append(path)
        sources = self.session.add_files(paths)
        self.assertEqual(sum(source["page_count"] for source in sources), 300)

        active = 0
        maximum = 0
        started = threading.Event()
        lock = threading.Lock()

        def slow_render(_source, page_index, kind):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                if active == 2:
                    started.set()
            time.sleep(0.06)
            with lock:
                active -= 1
            return "data:image/png;base64," + (f"{kind}-{page_index}-" * 10)

        requests = [
            (source["id"], page_index, "thumbnail" if page_index % 2 else "preview")
            for source in sources
            for page_index in range(8)
        ]
        with (
            patch.object(engine_module, "_PREVIEW_RENDER_SLOTS", threading.BoundedSemaphore(2)),
            patch.object(self.session, "_render_page_image", side_effect=slow_render),
            ThreadPoolExecutor(max_workers=24) as pool,
            TestClient(app) as client,
        ):
            futures = [
                pool.submit(self.session.page_image, source_id, page_index, kind)
                for source_id, page_index, kind in requests
            ]
            self.assertTrue(started.wait(1), "느린 미리보기 작업이 시작되지 않았습니다.")
            began = time.monotonic()
            response = client.get("/api/health")
            health_elapsed = time.monotonic() - began
            values = [future.result(timeout=3) for future in futures]

        self.assertEqual(response.status_code, 200)
        self.assertLess(health_elapsed, 1.0)
        self.assertEqual(len(values), 24)
        self.assertEqual(maximum, 2)
        self.assertLessEqual(self.session._preview_cache_bytes, 256)
        self.assertLess(len(requests), 300 * 2)


if __name__ == "__main__":
    unittest.main()
