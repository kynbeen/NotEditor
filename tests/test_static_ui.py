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

    def test_merge_workspace_has_source_and_preview_without_a_reorder_panel(self):
        self.assertIn("source-panel", self.html)
        self.assertIn("preview-panel", self.html)
        self.assertNotIn("result-panel", self.html)
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
        self.assertIn("[502, 503, 504].includes", self.js)
        self.assertIn("미리보기를 불러오지 못했습니다 · 다시 시도", self.js)
        self.assertIn(".image-load-error", self.css)

    def test_page_toggle_does_not_rebuild_left_document_list(self):
        toggle_body = self.js.split("function togglePage", 1)[1].split("function previewNode", 1)[0]
        self.assertNotIn("renderDocuments()", toggle_body)
        self.assertIn("updateDocumentSelectionUi(doc)", toggle_body)

    def test_merge_order_is_fixed_to_document_and_page_order(self):
        self.assertNotIn("item.draggable = true", self.js)
        self.assertNotIn("resetOrderButton", self.html)
        self.assertNotIn('class="drag-handle"', self.html)
        self.assertIn("state.order = defaultOrder()", self.js)

    def test_file_buttons_wait_for_runtime_connection(self):
        self.assertIn('id="addPdfButton" class="button secondary" type="button" disabled', self.html)
        self.assertIn('id="emptyAddButton" class="button primary" type="button" disabled', self.html)
        self.assertIn('callApi("health")', self.js)
        self.assertIn("NotEditor 연결을 확인할 수 없습니다", self.js)
        self.assertIn('runtime: window.location.hash === "#desktop"', self.js)

    def test_summary_ai_startup_plan_populates_the_desktop_merge_ui(self):
        self.assertIn("startup_plan: async () => ({ ok: true, plan: null })", self.js)
        self.assertIn('callApi("startup_plan")', self.js)
        self.assertIn("function applyStartupPlan(plan)", self.js)
        self.assertIn("state.selected = new Set(state.order.map(refKey))", self.js)
        self.assertIn("Boolean(state.mergePlan)", self.js)

    def test_summary_ai_source_review_combines_page_pairs_with_merge_selection(self):
        self.assertIn('id="sourceReview"', self.html)
        self.assertIn('id="sourceReviewRows"', self.html)
        self.assertIn("실제 사용한 파일", self.html)
        self.assertIn("현재 수집함 파일", self.html)
        self.assertIn("function renderSourceReview()", self.js)
        self.assertIn('callApi("page_image", pageRef.document_id', self.js)
        self.assertIn('callApi("finish_review", decision, state.order)', self.js)
        self.assertIn('plan.origin === "merged" ? "합쳐서 갱신" : "전체 갱신"', self.js)
        self.assertIn("넘어가기", self.html)
        self.assertIn(".workspace.review-mode", self.css)
        self.assertIn("수집함 PDF 쪽 선택", self.js)

    def test_source_review_uses_a_large_left_comparison_and_right_page_picker(self):
        self.assertIn("grid-template-columns: minmax(650px, 2.7fr) minmax(300px, .8fr)", self.css)
        self.assertIn(".workspace.review-mode .source-panel { grid-column: 2", self.css)
        self.assertIn(".workspace.review-mode .preview-panel { display: none; }", self.css)
        self.assertIn(".source-review .page-review-rows { min-height: 0; flex: 1; overflow-y: auto;", self.css)
        self.assertIn(".source-review .review-page { height: clamp(360px", self.css)
        self.assertIn("position: sticky; top: 0; z-index: 30;", self.css)   # 도구 막대 고정
        self.assertIn('{ root: refs.sourceReview, rootMargin: "600px 0px" }', self.js)

    def test_excluded_picker_pages_dim_the_current_inbox_review_preview(self):
        self.assertIn('cell.dataset.key = pageKey(pageRef.document_id, pageRef.page_index)', self.js)
        self.assertIn("function updateSourceReviewSelection(onlyKey = null)", self.js)
        self.assertIn('cell.classList.toggle("excluded", !selected)', self.js)
        self.assertIn("updateSourceReviewSelection(key)", self.js)
        self.assertIn(".source-review .review-cell.target-cell.excluded .review-page", self.css)

    def test_source_review_skip_explains_that_it_keeps_the_file_and_accepts_the_source(self):
        self.assertIn("파일은 유지하고 현재 수집함 버전을 원본 최신으로 확인합니다", self.html)
        self.assertIn("파일은 유지하고 현재 수집함 버전을 원본 최신으로 확인했습니다", self.js)

    def test_changed_pages_can_be_jumped_to_directly(self):
        self.assertIn('id="sourceReviewPrev"', self.html)
        self.assertIn('id="sourceReviewNext"', self.html)
        self.assertIn("function jumpToChangedPage(step)", self.js)
        self.assertIn('row.scrollIntoView({ behavior: "smooth", block: "center" })', self.js)
        self.assertIn("state.sourceReviewChanged.push(index)", self.js)

    def test_merged_sources_flag_changes_inside_or_beside_the_recorded_range(self):
        self.assertIn("function recordedImpact(pair, ranges)", self.js)
        self.assertIn('entry.pages.has(index - 1) || entry.pages.has(index + 1)', self.js)
        self.assertIn('id="sourceReviewRangeNote"', self.html)
        self.assertIn(".review-row.range-impact.inside", self.css)
        self.assertIn("#ff5070", self.css)
        self.assertIn("#32d2c9", self.css)
        self.assertIn("#a99cff", self.css)
        app = (Path(__file__).parents[1] / "noteditor" / "app.py").read_text(encoding="utf-8")
        self.assertIn('"recorded_ranges": recorded', app)

    def test_a_handoff_session_stays_on_merge_and_returns_to_summary_ai(self):
        self.assertIn("function lockToMergeTool()", self.js)
        self.assertIn("refs.handwriting.disabled = true", self.js)
        self.assertIn('refs.save.textContent = "저장하고 summary.ai로 돌아가기"', self.js)
        self.assertIn("async function returnToSummaryAi(message)", self.js)
        self.assertIn('callApi("close_window")', self.js)
        app = (Path(__file__).parents[1] / "noteditor" / "app.py").read_text(encoding="utf-8")
        self.assertIn("def close_window(self) -> dict:", app)
        self.assertIn("summary.ai 인계로 열린 창에서만 쓸 수 있습니다.", app)

    def test_empty_summary_ai_merge_plan_opens_the_inbox_picker_immediately(self):
        self.assertIn('if (plan.auto_choose) setTimeout(() => { void addPdfs(); }, 0)', self.js)
        app = (Path(__file__).parents[1] / "noteditor" / "app.py").read_text(encoding="utf-8")
        self.assertIn('directory=str(self._input_root or "")', app)
        self.assertIn("summary.ai 수집함 밖의 PDF는 사용할 수 없습니다", app)

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

    def test_buttons_keep_their_labels_on_one_line(self):
        self.assertIn("button { color: inherit; white-space: nowrap; }", self.css)
        self.assertIn("refs.mergeOutputName.parentElement.hidden = true", self.js)

    def test_alignment_review_is_continuous_side_by_side_and_shows_actual_ink(self):
        self.assertIn('id="handwritingReview"', self.html)
        self.assertIn('id="handwritingReviewRows"', self.html)
        self.assertIn('id="reviewInkToggle"', self.html)
        self.assertIn("옛 문서 · 필기 원본", self.html)
        self.assertIn("새 PDF · 드래그하여 순서 수정", self.html)
        self.assertIn('"handwriting_preview", targetIndex, sourceIndex', self.js)
        self.assertIn("sourcePage.querySelector(\".review-ink\").src = response.ink", self.js)
        self.assertIn("targetPage.querySelector(\".review-ink\").src = response.ink", self.js)
        self.assertIn(".review-page img { position: absolute", self.css)
        self.assertIn(".page-review.hide-ink img.review-ink", self.css)
        self.assertNotIn('addEventListener("wheel"', self.js)
        self.assertIn("화면은 자유롭게 계속 스크롤할 수 있습니다", self.html)

    def test_alignment_review_lazy_loads_without_blocking_scroll(self):
        self.assertIn("new IntersectionObserver", self.js)
        self.assertIn('rootMargin: "600px 0px"', self.js)
        self.assertIn("queuePreviewRequest", self.js)
        self.assertIn("보이면 미리보기를 불러옵니다", self.js)
        self.assertNotIn("alignPageScrubber", self.js)

    def test_desktop_alignment_review_fits_three_page_pairs_in_the_workspace(self):
        self.assertIn(
            ".review-page { position: relative; height: clamp(118px, 13vw, 132px)",
            self.css,
        )
        self.assertIn(".review-page { height: min(78vw, 360px); }", self.css)

    def test_alignment_card_reports_scale_and_warnings(self):
        self.assertIn("본문 기준으로", self.js)
        self.assertIn("본문 오차 최대", self.js)
        self.assertIn("폭 기준으로 맞췄습니다", self.js)
        self.assertIn("잘립니다", self.js)

    def test_page_review_supports_confirmation_and_target_drag_reordering(self):
        self.assertIn('id="handwritingReview"', self.html)
        self.assertIn("function shiftedTargetPlan", self.js)
        self.assertIn('target.addEventListener("dragstart"', self.js)
        self.assertIn("개 대응이 달라집니다", self.js)
        self.assertIn("변경된 행은 다시 확인해야 합니다", self.js)
        self.assertIn('id="reviewReorderDialog"', self.html)
        self.assertIn("await confirmReviewReorder(", self.js)
        self.assertIn("계속 변경", self.html)
        self.assertIn('className = "review-confirm"', self.js)
        self.assertIn("확인하지 않은 쪽 대응", self.js)
        self.assertIn("unconfirmedPages", self.js)
        for summary in ("전체", "자동 연결", "새 전용", "옛 전용", "확인 필요"):
            self.assertIn(summary, self.js)
        self.assertIn("page_plan: pagePlan", self.js)
        self.assertIn("allow_unconfirmed: allowUnconfirmed", self.js)

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

    def test_goodnotes_is_offered_but_marked_experimental(self):
        """앱 왕복 검증 전까지는 고르기 전에 실험 기능임이 보여야 한다."""
        self.assertIn(".goodnotes", self.html)
        self.assertIn("Goodnotes 6는 실험 기능입니다", self.html)
        self.assertIn("transfer-experimental", self.css)

    def test_supported_goodnotes_version_is_named_on_screen(self):
        """어느 버전이 되는지는 화면에서 바로 보여야 한다. 6에서만 확인했다."""
        self.assertIn("GOODNOTES 6", self.html)

    def test_page_review_can_exclude_and_restore_result_rows(self):
        self.assertIn('exclude.className = "review-exclude"', self.js)
        self.assertIn("targetMeta.append(exclude, confirm)", self.js)
        self.assertIn('slot.excluded = !slot.excluded', self.js)
        self.assertIn('slot.excluded ? "다시 포함" : "결과에서 제외"', self.js)
        self.assertIn(".review-row.excluded", self.css)

    def test_saved_handwriting_name_follows_the_source_format(self):
        """확장자를 파일명에서 추측하지 않고 서버가 알려준 형식으로 정한다."""
        self.assertIn("source_format: response.source_format", self.js)
        self.assertIn("handwritingOutputExtension()", self.js)
        # 형식을 늘릴 때 두 군데를 따로 고치다 어긋나지 않도록 목록 하나만 본다.
        self.assertIn(
            'HANDWRITING_EXTENSIONS = ["sdocx", "notewise", "goodnotes"]', self.js
        )

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
