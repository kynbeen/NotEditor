from __future__ import annotations

import inspect
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote

from fastapi.testclient import TestClient

from noteditor.web import SESSION_COOKIE, app, health, health_head, store
from tests.test_notewise_transfer import _make_notewise, _make_pdf as make_notewise_pdf
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

    def wait_for_handwriting_analysis(self) -> dict:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = self.client.get("/api/handwriting/status")
            self.assertEqual(response.status_code, 200, response.text)
            status = response.json()
            if status["analysis"]["state"] != "running":
                return status
            time.sleep(0.02)
        self.fail("필기 분석이 끝나지 않았습니다.")

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

    def test_health_handlers_never_wait_for_the_worker_pool(self):
        self.assertTrue(inspect.iscoroutinefunction(health))
        self.assertTrue(inspect.iscoroutinefunction(health_head))

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
        status = self.wait_for_handwriting_analysis()
        self.assertTrue(status["ready"], status)

        preview = self.client.get("/api/handwriting/preview?page_index=0&source_index=-2")
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.json()["ink"].startswith("data:image/png;base64,"))

        exported = self.client.post(
            "/api/handwriting/export",
            json={"suggested_name": "moved.sdocx", "target_mapping": None},
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertTrue(exported.content.startswith(b"PK"))

    def test_export_without_a_source_answers_with_an_error_not_a_crash(self):
        """원본을 안 고른 채 저장을 누르면 400과 안내 문구가 나가야 한다."""
        exported = self.client.post(
            "/api/handwriting/export",
            json={"suggested_name": "moved.sdocx", "target_mapping": None},
        )
        self.assertEqual(exported.status_code, 400, exported.text)
        body = exported.json()
        self.assertFalse(body["ok"])
        self.assertIn("선택", body["error"])

    def test_analysis_failure_keeps_web_uploads_and_retry_uses_the_same_files(self):
        source = self.root / "retry.sdocx"
        source.write_bytes(b"synthetic sdocx")
        inspection = SimpleNamespace(as_dict=lambda: {"page_count": 2, "mode": "exact"})
        attempts = 0

        def inspect(_source, _target, *, progress):
            nonlocal attempts
            attempts += 1
            progress("structure")
            if attempts == 1:
                raise RuntimeError("합성 분석 실패")
            progress("matching")
            progress("alignment")
            progress("preview")
            return inspection

        with patch("noteditor.app.inspect_transfer", side_effect=inspect):
            uploaded_source = self.client.post(
                "/api/handwriting/source",
                files={"file": (source.name, source.read_bytes(), "application/zip")},
            )
            self.assertEqual(uploaded_source.status_code, 200, uploaded_source.text)
            uploaded_target = self.client.post(
                "/api/handwriting/target",
                files={"file": ("target.pdf", self.pdf.read_bytes(), "application/pdf")},
            )
            self.assertEqual(uploaded_target.status_code, 200, uploaded_target.text)
            failed = self.wait_for_handwriting_analysis()
            self.assertEqual(failed["analysis"]["state"], "error")
            self.assertEqual(failed["source_name"], source.name)
            self.assertEqual(failed["target_name"], "target.pdf")
            retry = self.client.post("/api/handwriting/retry")
            self.assertEqual(retry.status_code, 200, retry.text)
            ready = self.wait_for_handwriting_analysis()

        self.assertTrue(ready["ready"], ready)
        self.assertEqual(attempts, 2)
        session = store._sessions[self.client.cookies.get(SESSION_COOKIE)]
        self.assertTrue(session.api._handwriting_source.exists())
        self.assertTrue(session.api._handwriting_target.exists())

    def test_notewise_download_name_never_carries_the_other_format(self):
        """화면이 `.sdocx` 가 붙은 이름을 보내와도 `.sdocx.notewise` 로 내려가면 안 된다."""
        notewise_pdf = self.root / "notewise.pdf"
        make_notewise_pdf(notewise_pdf, "same text")
        source_notewise = self.root / "annotated.notewise"
        _make_notewise(source_notewise, notewise_pdf)

        status = self.client.post(
            "/api/handwriting/source",
            files={"file": ("annotated.notewise", source_notewise.read_bytes(), "application/zip")},
        ).json()
        self.assertEqual(status["source_format"], "notewise")
        self.client.post(
            "/api/handwriting/target",
            files={"file": ("target.pdf", notewise_pdf.read_bytes(), "application/pdf")},
        )
        self.assertTrue(self.wait_for_handwriting_analysis()["ready"])

        for suggested in ("target-필기.sdocx", "target-필기.notewise", "target-필기"):
            with self.subTest(suggested=suggested):
                exported = self.client.post(
                    "/api/handwriting/export",
                    json={"suggested_name": suggested, "target_mapping": None},
                )
                self.assertEqual(exported.status_code, 200, exported.text)
                disposition = unquote(exported.headers["content-disposition"])
                self.assertIn("target-필기.notewise", disposition)
                self.assertNotIn(".sdocx", disposition)

    def test_upload_preview_and_export_notewise(self):
        notewise_pdf = self.root / "notewise.pdf"
        make_notewise_pdf(notewise_pdf, "same text")
        source_notewise = self.root / "annotated.notewise"
        _make_notewise(source_notewise, notewise_pdf)

        source = self.client.post(
            "/api/handwriting/source",
            files={"file": ("annotated.notewise", source_notewise.read_bytes(), "application/zip")},
        )
        self.assertEqual(source.status_code, 200, source.text)
        self.assertFalse(source.json()["ready"])

        target = self.client.post(
            "/api/handwriting/target",
            files={"file": ("target.pdf", notewise_pdf.read_bytes(), "application/pdf")},
        )
        self.assertEqual(target.status_code, 200, target.text)
        status = self.wait_for_handwriting_analysis()
        self.assertTrue(status["ready"], status)

        preview = self.client.get("/api/handwriting/preview?page_index=0&source_index=-2")
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.json()["ink"].startswith("data:image/png;base64,"))

        exported = self.client.post(
            "/api/handwriting/export",
            json={"suggested_name": "moved.notewise", "target_mapping": None},
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertIn("moved.notewise", exported.headers["content-disposition"])
        self.assertTrue(exported.content.startswith(b"PK"))


class WorkspaceIsolationTests(unittest.TestCase):
    """접속자마다 하나씩만, 남기지 않고, 남에게 새지 않게."""

    def setUp(self):
        store.close()
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.pdf = self.root / "source.pdf"
        make_pdf(self.pdf, ["ONE", "TWO"])

    def tearDown(self):
        store.close()
        self.folder.cleanup()

    @staticmethod
    def _files(temp_dir: Path) -> list[str]:
        return sorted(str(p.relative_to(temp_dir)) for p in temp_dir.rglob("*") if p.is_file())

    def _only_workspace(self) -> Path:
        self.assertEqual(len(store._sessions), 1, "작업공간이 정확히 하나여야 합니다.")
        return next(iter(store._sessions.values())).api._session.temp_dir

    def _upload(self, client: TestClient) -> str:
        response = client.post(
            "/api/documents",
            files={"files": ("source.pdf", self.pdf.read_bytes(), "application/pdf")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["sources"][0]["id"]

    def test_a_first_load_creates_one_workspace_even_before_the_cookie_lands(self):
        """브라우저는 첫 화면과 정적 자산을 쿠키가 정해지기 전에 한꺼번에 요청한다.

        그때 자산마다 작업공간이 생기면, 접속 한 번에 아무도 쓰지 않는 임시 폴더가
        여러 개 만들어져 세션 수명(기본 2시간) 내내 디스크에 남는다.
        """
        for path in ("/", "/app.js", "/app.css", "/sw.js", "/manifest.webmanifest"):
            client = TestClient(app)  # 아직 쿠키를 받지 못한 병렬 요청
            self.assertEqual(client.get(path).status_code, 200, path)
        self.assertEqual(len(store._sessions), 1)

    def test_uptime_pings_never_create_a_workspace(self):
        client = TestClient(app)
        with client:
            for _ in range(5):
                client.cookies.clear()
                client.get("/api/health")
            self.assertEqual(len(store._sessions), 0)

    def test_static_assets_alone_create_no_workspace(self):
        client = TestClient(app)
        with client:
            client.cookies.clear()
            client.get("/app.js")
            self.assertEqual(len(store._sessions), 0)

    def test_per_user_responses_forbid_shared_caching(self):
        """첫 화면은 세션 쿠키를 실어 보낸다. 공용 캐시에 저장되면 모두가 한 작업공간을 쓴다."""
        with TestClient(app) as browser:
            for path in ("/", "/api/health"):
                response = browser.get(path)
                if path == "/api/health":
                    continue
                self.assertIn("no-store", response.headers.get("cache-control", ""))
                self.assertEqual(response.headers.get("vary"), "Cookie")
            document_id = self._upload(browser)
            image = browser.get(f"/api/documents/{document_id}/pages/0?kind=thumbnail")
            self.assertIn("no-store", image.headers.get("cache-control", ""))
            self.assertEqual(image.headers.get("vary"), "Cookie")

    def test_two_visitors_never_see_each_other_documents(self):
        other = self.root / "other.pdf"
        make_pdf(other, ["ZZZ"])
        with TestClient(app) as alice, TestClient(app) as bob:
            alice.get("/")
            bob.get("/")
            alice_docs = alice.post(
                "/api/documents",
                files={"files": ("alice.pdf", self.pdf.read_bytes(), "application/pdf")},
            ).json()["sources"]
            bob_docs = bob.post(
                "/api/documents",
                files={"files": ("bob.pdf", other.read_bytes(), "application/pdf")},
            ).json()["sources"]
        self.assertEqual([doc["name"] for doc in alice_docs], ["alice.pdf"])
        self.assertEqual([doc["name"] for doc in bob_docs], ["bob.pdf"])

    def test_removing_a_document_also_deletes_its_uploaded_copy(self):
        with TestClient(app) as browser:
            document_id = self._upload(browser)
            workspace = self._only_workspace()
            self.assertEqual(len(self._files(workspace)), 1)
            self.assertEqual(browser.delete(f"/api/documents/{document_id}").status_code, 200)
            self.assertEqual(self._files(workspace), [])

    def test_rejected_upload_does_not_stay_on_disk(self):
        broken = self.root / "broken.pdf"
        broken.write_bytes(b"not a pdf at all")
        with TestClient(app) as browser:
            browser.get("/")
            response = browser.post(
                "/api/documents",
                files={"files": ("broken.pdf", broken.read_bytes(), "application/pdf")},
            )
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(self._files(self._only_workspace()), [])

    def _load_both_tools(self, browser: TestClient) -> None:
        self._upload(browser)
        response = browser.post(
            "/api/handwriting/target",
            files={"file": ("target.pdf", self.pdf.read_bytes(), "application/pdf")},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_clearing_documents_leaves_the_handwriting_tool_untouched(self):
        with TestClient(app) as browser:
            self._load_both_tools(browser)
            session = next(iter(store._sessions.values()))
            workspace = session.api._session.temp_dir
            self.assertEqual(len(self._files(workspace)), 2)

            result = browser.post("/api/documents/reset")
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(result.json()["ok"])

            self.assertEqual(session.api._session.sources, [])
            self.assertIsNotNone(session.api._handwriting_target, "필기 선택이 사라졌습니다.")
            remaining = self._files(workspace)
            self.assertEqual(len(remaining), 1, remaining)
            self.assertTrue(remaining[0].startswith("uploads"))
            self.assertIn("handwriting", remaining[0])
            self.assertEqual(len(store._sessions), 1, "초기화가 세션을 끊어서는 안 됩니다.")

    def test_clearing_handwriting_leaves_the_merge_tool_untouched(self):
        with TestClient(app) as browser:
            self._load_both_tools(browser)
            session = next(iter(store._sessions.values()))
            workspace = session.api._session.temp_dir

            result = browser.post("/api/handwriting/reset")
            self.assertEqual(result.status_code, 200, result.text)

            self.assertIsNone(session.api._handwriting_target)
            self.assertEqual(len(session.api._session.sources), 1, "올린 PDF가 사라졌습니다.")
            remaining = self._files(workspace)
            self.assertEqual(len(remaining), 1, remaining)
            self.assertIn("documents", remaining[0])

    def test_replacing_a_handwriting_file_drops_the_previous_one(self):
        """잘못 올린 파일을 곧바로 다시 올리면 앞엣것이 디스크에 남으면 안 된다."""
        with TestClient(app) as browser:
            browser.get("/")
            for name in ("first.pdf", "second.pdf", "third.pdf"):
                response = browser.post(
                    "/api/handwriting/target",
                    files={"file": (name, self.pdf.read_bytes(), "application/pdf")},
                )
                self.assertEqual(response.status_code, 200, response.text)
                remaining = self._files(self._only_workspace())
                self.assertEqual(len(remaining), 1, remaining)
                self.assertTrue(remaining[0].endswith(name), remaining)

    def test_workspace_count_is_capped(self):
        with patch("noteditor.web.MAX_SESSIONS", 3):
            clients = []
            for _ in range(6):
                client = TestClient(app)
                client.get("/")
                clients.append(client)
            self.assertLessEqual(len(store._sessions), 3)

    def test_idle_workspaces_are_swept_without_any_request(self):
        with TestClient(app) as browser:
            self._upload(browser)
            workspace = self._only_workspace()
            with patch("noteditor.web.SESSION_TTL_SECONDS", -1):
                store.expire_idle()
            self.assertEqual(len(store._sessions), 0)
            self.assertFalse(workspace.exists())


if __name__ == "__main__":
    unittest.main()
