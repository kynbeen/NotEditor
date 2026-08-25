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
        self.assertIn("filter: grayscale(1)", self.css)

    def test_result_pages_are_draggable_and_resettable(self):
        self.assertIn("item.draggable = true", self.js)
        self.assertIn("resetOrderButton", self.html)
        self.assertIn("state.order = defaultOrder()", self.js)

    def test_file_buttons_wait_for_desktop_bridge(self):
        self.assertIn('id="addPdfButton" class="button secondary" type="button" disabled', self.html)
        self.assertIn('id="emptyAddButton" class="button primary" type="button" disabled', self.html)
        self.assertIn('callApi("health")', self.js)
        self.assertIn("서버를 실행할 필요는 없습니다", self.html)


if __name__ == "__main__":
    unittest.main()
