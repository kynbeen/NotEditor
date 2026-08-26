import unittest
from pathlib import Path


class StaticUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        static = Path(__file__).parents[1] / "pdf_page_composer" / "static"
        cls.html = (static / "index.html").read_text(encoding="utf-8")
        cls.css = (static / "app.css").read_text(encoding="utf-8")
        cls.js = (static / "app.js").read_text(encoding="utf-8")

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

    def test_file_buttons_wait_for_desktop_bridge(self):
        self.assertIn('id="addPdfButton" class="button secondary" type="button" disabled', self.html)
        self.assertIn('id="emptyAddButton" class="button primary" type="button" disabled', self.html)
        self.assertIn('callApi("health")', self.js)
        self.assertIn("서버를 실행할 필요는 없습니다", self.html)

    def test_handwriting_transfer_has_guided_desktop_flow(self):
        self.assertIn('id="handwritingButton"', self.html)
        self.assertIn('id="handwritingDialog"', self.html)
        self.assertIn("필기와 형광펜 옮기기", self.html)
        self.assertIn('callApi("save_handwriting_transfer"', self.js)
        self.assertIn("크기나 여백이 달라지면 본문을 기준으로 자동 정렬", self.js)
        self.assertIn(".compatibility-card.ready", self.css)

    def test_alignment_preview_overlays_old_and_new_background(self):
        self.assertIn('id="handwritingPreview"', self.html)
        self.assertIn('id="alignBefore"', self.html)
        self.assertIn('id="alignAfter"', self.html)
        self.assertIn('id="alignBlend"', self.html)
        self.assertIn('callApi("handwriting_preview"', self.js)
        self.assertIn("refs.alignAfter.style.opacity", self.js)
        self.assertIn(".align-stage img { position: absolute", self.css)

    def test_alignment_card_reports_scale_and_warnings(self):
        self.assertIn("본문 기준으로", self.js)
        self.assertIn("본문 오차 최대", self.js)
        self.assertIn("폭 기준으로 맞췄습니다", self.js)
        self.assertIn("잘립니다", self.js)


if __name__ == "__main__":
    unittest.main()
