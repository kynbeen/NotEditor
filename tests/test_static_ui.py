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

    def test_image_requests_are_lazy_bounded_deduplicated_and_retryable_inline(self):
        self.assertIn("function createRequestQueue(limit)", self.js)
        self.assertIn("createRequestQueue(3)", self.js)
        self.assertIn("createRequestQueue(1)", self.js)
        self.assertIn("state.imageInflight.has(key)", self.js)
        self.assertIn("CLIENT_IMAGE_CACHE_BYTES", self.js)
        self.assertIn("lazyImageObserver(refs.documentList)", self.js)
        self.assertIn("lazyImageObserver(refs.resultList)", self.js)
        self.assertIn("[502, 503, 504].includes", self.js)
        self.assertIn("미리보기를 불러오지 못했습니다 · 다시 시도", self.js)
        self.assertIn(".image-load-error", self.css)

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

    def test_handwriting_upload_and_analysis_have_distinct_retryable_stages(self):
        self.assertIn("new XMLHttpRequest()", self.js)
        self.assertIn("request.upload.addEventListener", self.js)
        self.assertIn("필기 파일 업로드 중…", self.js)
        self.assertIn('handwriting_status: () => fetchJson("/api/handwriting/status")', self.js)
        self.assertIn('callApi("handwriting_status")', self.js)
        self.assertIn('callApi("retry_handwriting_analysis")', self.js)
        for message in (
            "파일 구조 확인 중…",
            "페이지 비교·자동 매칭 중…",
            "필기 좌표 정렬 중…",
            "미리보기 준비 중…",
        ):
            self.assertIn(message, (Path(__file__).parents[1] / "noteditor" / "app.py").read_text(encoding="utf-8"))
        self.assertIn('id="retryHandwritingButton"', self.html)

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

    def test_each_tool_clears_only_its_own_files(self):
        """초기화는 도구별이다. 한쪽 버튼이 다른 쪽 선택까지 지우면 안 된다."""
        self.assertIn('id="resetDocumentsButton"', self.html)
        self.assertIn('id="resetHandwritingButton"', self.html)
        self.assertIn('id="resetDocumentsButton" class="button utility"', self.html)
        self.assertIn('id="resetHandwritingButton" class="button utility"', self.html)
        self.assertIn('refs.resetDocuments.addEventListener("click", resetDocuments)', self.js)
        self.assertIn('refs.resetHandwriting.addEventListener("click", resetHandwritingTransfer)', self.js)
        self.assertIn('"/api/documents/reset"', self.js)
        self.assertIn('"/api/handwriting/reset"', self.js)
        # 전체를 한 번에 지우는 버튼은 두지 않는다.
        self.assertNotIn("resetWorkspaceButton", self.html)
        self.assertNotIn("/api/session/reset", self.js)
        # 되돌릴 수 없는 동작이므로 확인을 한 번 받는다.
        self.assertIn("window.confirm", self.js)

    def test_saved_handwriting_name_follows_the_source_format(self):
        """확장자를 파일명에서 추측하지 않고 서버가 알려준 형식으로 정한다."""
        self.assertIn("source_format: response.source_format", self.js)
        self.assertIn('state.handwriting.source_format === "notewise"', self.js)

    def test_output_names_are_editable_without_letting_extensions_drift(self):
        self.assertIn('id="mergeOutputName"', self.html)
        self.assertIn('id="handwritingOutputName"', self.html)
        self.assertIn('id="handwritingOutputSuffix"', self.html)
        self.assertIn("updateMergeOutputName", self.js)
        self.assertIn("updateHandwritingOutputName", self.js)
        self.assertIn("withoutKnownExtension(refs.mergeOutputName.value.trim())", self.js)
        self.assertIn("withoutKnownExtension(refs.handwritingOutputName.value.trim())", self.js)
        self.assertIn(".output-name-field", self.css)


if __name__ == "__main__":
    unittest.main()
