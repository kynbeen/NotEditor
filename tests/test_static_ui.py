import json
import unittest
from pathlib import Path


class StaticUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        static = Path(__file__).parents[1] / "noteditor" / "static"
        cls.html = (static / "index.html").read_text(encoding="utf-8")
        cls.css = (static / "app.css").read_text(encoding="utf-8")
        cls.js = (static / "app.js").read_text(encoding="utf-8")
        cls.manifest = json.loads((static / "manifest.webmanifest").read_text(encoding="utf-8"))
        cls.service_worker = (static / "sw.js").read_text(encoding="utf-8")

    def test_has_three_column_source_preview_result_layout(self):
        self.assertIn("source-panel", self.html)
        self.assertIn("preview-panel", self.html)
        self.assertIn("result-panel", self.html)
        self.assertIn("grid-template-columns", self.css)

    def test_selected_pages_are_bright_and_unselected_pages_dimmed(self):
        self.assertIn(".page-tile:not(.selected)", self.css)
        self.assertIn(".preview-page:not(.selected)", self.css)
        self.assertIn("filter: grayscale(1)", self.css)

    def test_preview_contains_every_uploaded_page_and_scrolls_between_them(self):
        self.assertIn('id="previewPages"', self.html)
        self.assertIn("state.documents.forEach((doc) =>", self.js)
        self.assertIn('className = `preview-page', self.js)
        self.assertIn("new IntersectionObserver", self.js)
        self.assertIn('refs.previewStage.addEventListener("scroll"', self.js)
        self.assertIn('scrollIntoView({ behavior: "smooth"', self.js)

    def test_page_toggle_does_not_rebuild_left_document_list(self):
        toggle_body = self.js.split("function togglePage", 1)[1].split("function previewNode", 1)[0]
        self.assertNotIn("renderDocuments()", toggle_body)
        self.assertIn("updateDocumentSelectionUi(doc)", toggle_body)

    def test_result_pages_are_draggable_and_resettable(self):
        self.assertIn("item.draggable = true", self.js)
        self.assertIn("resetOrderButton", self.html)
        self.assertIn("state.order = defaultOrder()", self.js)

    def test_file_buttons_wait_for_runtime_connection(self):
        self.assertIn('id="addPdfButton" class="button secondary" type="button" disabled', self.html)
        self.assertIn('id="emptyAddButton" class="button primary" type="button" disabled', self.html)
        self.assertIn('callApi("health")', self.js)
        self.assertIn("NotEditor 연결을 확인할 수 없습니다", self.js)
        self.assertIn('runtime: window.location.hash === "#desktop"', self.js)

    def test_merge_and_handwriting_are_peer_tabs(self):
        self.assertIn('id="handwritingButton"', self.html)
        self.assertIn('id="mergeTabButton"', self.html)
        self.assertIn('id="mergeWorkspace"', self.html)
        self.assertIn('id="handwritingWorkspace"', self.html)
        self.assertNotIn('<dialog id="handwritingDialog"', self.html)
        self.assertIn('role="tablist"', self.html)
        self.assertIn('showTool("handwriting")', self.js)

    def test_handwriting_transfer_has_guided_dual_runtime_flow(self):
        self.assertIn("필기와 형광펜 옮기기", self.html)
        self.assertIn('callApi("save_handwriting_transfer"', self.js)
        self.assertIn("크기나 여백이 달라지면 본문을 기준으로 자동 정렬", self.js)
        self.assertIn(".compatibility-card.ready", self.css)
        self.assertIn('choose_handwriting_target: () => uploadWebFiles', self.js)
        self.assertIn('setBusy(true, kind === "source"', self.js)

    def test_result_content_starts_at_same_header_boundary(self):
        self.assertNotIn('class="result-toolbar"', self.html)
        self.assertIn('class="panel-header-actions"', self.html)
        self.assertIn(".panel-header { height: 73px", self.css)

    def test_alignment_preview_overlays_backgrounds_and_actual_ink(self):
        self.assertIn('id="handwritingPreview"', self.html)
        self.assertIn('id="alignBefore"', self.html)
        self.assertIn('id="alignAfter"', self.html)
        self.assertIn('id="alignInk"', self.html)
        self.assertIn('id="alignBlend"', self.html)
        self.assertIn('callApi("handwriting_preview"', self.js)
        self.assertIn("refs.alignInk.src = response.ink", self.js)
        self.assertIn("refs.alignAfter.style.opacity", self.js)
        self.assertIn(".align-stage img { position: absolute", self.css)
        self.assertIn('refs.alignStage.addEventListener("wheel"', self.js)
        self.assertIn("event.deltaY > 0 ? 1 : -1", self.js)
        self.assertIn("{ passive: false }", self.js)
        self.assertIn("휠, 쪽 번호, 오른쪽 스크롤바로 이동할 수 있습니다", self.html)

    def test_alignment_preview_has_loading_jump_and_drag_scroll_controls(self):
        self.assertIn('id="alignLoading"', self.html)
        self.assertIn('id="alignPageInput"', self.html)
        self.assertIn('id="alignPageScrubber"', self.html)
        self.assertIn("function jumpToAlignPage", self.js)
        self.assertIn('refs.alignPageScrubber.addEventListener("input"', self.js)
        self.assertIn("writing-mode: vertical-lr", self.css)

    def test_alignment_card_reports_scale_and_warnings(self):
        self.assertIn("본문 기준으로", self.js)
        self.assertIn("본문 오차 최대", self.js)
        self.assertIn("폭 기준으로 맞췄습니다", self.js)
        self.assertIn("잘립니다", self.js)

    def test_rebuild_match_table_supports_manual_source_selection(self):
        self.assertIn('id="handwritingMatchEditor"', self.html)
        self.assertIn('id="handwritingMatchRows"', self.html)
        self.assertIn("function validateMatchMapping", self.js)
        self.assertIn("state.handwriting.matchMapping", self.js)
        self.assertIn("같은 구판 쪽을 두 번 선택할 수 없습니다", self.js)

    def test_web_ui_is_installable_as_a_pwa(self):
        self.assertIn('rel="manifest" href="manifest.webmanifest"', self.html)
        self.assertIn('rel="apple-touch-icon"', self.html)
        self.assertEqual(self.manifest["display"], "standalone")
        self.assertEqual(self.manifest["start_url"], "/index.html")
        self.assertEqual(
            {icon["sizes"] for icon in self.manifest["icons"]},
            {"192x192", "512x512"},
        )
        self.assertIn('navigator.serviceWorker.register("/sw.js")', self.js)
        self.assertIn('window.location.hash === "#desktop"', self.js)
        self.assertIn('callApi("toggle_fullscreen")', self.js)

    def test_service_worker_never_caches_api_or_upload_responses(self):
        self.assertIn('url.pathname.startsWith("/api/")', self.service_worker)
        self.assertIn('event.request.method !== "GET"', self.service_worker)
        self.assertIn('caches.match("/index.html")', self.service_worker)

    def test_service_worker_skips_responses_marked_per_user(self):
        self.assertIn('includes("no-store")', self.service_worker)
        self.assertIn("!noStore", self.service_worker)

    def test_workspace_reset_is_reachable_from_both_tools(self):
        """도구 탭을 바꿔도 남아 있어야 한다. 문서 합치기 전용 버튼 묶음 밖에 둔다."""
        self.assertIn('id="resetWorkspaceButton"', self.html)
        self.assertIn("top-actions-area", self.html)
        self.assertIn(".top-actions-area", self.css)
        self.assertIn('refs.resetWorkspace.addEventListener("click", resetWorkspace)', self.js)
        self.assertIn('"/api/session/reset"', self.js)
        # 되돌릴 수 없는 동작이므로 확인을 한 번 받는다.
        self.assertIn("window.confirm", self.js)


if __name__ == "__main__":
    unittest.main()
