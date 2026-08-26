from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pymupdf

from noteditor.alignment import (
    build_aligned_pdf,
    estimate_alignment,
    ink_box,
    points_to_mm,
)

PAGE_WIDTH = 720.0
PAGE_HEIGHT = 540.0


def make_document(path: Path, pages: int = 4, *, width=PAGE_WIDTH, height=PAGE_HEIGHT) -> Path:
    """본문이 페이지마다 다른 양으로 들어간 문서. 여백은 일정하다."""
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page(width=width, height=height)
        for line in range(3 + index):
            page.insert_text((90, 120 + line * 30), f"본문 {index + 1}-{line + 1} BODY", fontsize=18)
        page.draw_rect(pymupdf.Rect(90, 100, width - 90, height - 100), width=1.5)
    document.save(path)
    document.close()
    return path


def relayout(source: Path, output: Path, *, page_scale: float, content_scale: float,
             pad_x: float, pad_y: float) -> Path:
    """같은 내용을 다른 페이지 크기·다른 본문 배치로 다시 그린다."""
    with pymupdf.open(source) as origin, pymupdf.open() as document:
        for index in range(origin.page_count):
            rect = origin[index].rect
            page = document.new_page(width=rect.width * page_scale, height=rect.height * page_scale)
            destination = pymupdf.Rect(
                pad_x, pad_y,
                pad_x + rect.width * content_scale,
                pad_y + rect.height * content_scale,
            )
            page.show_pdf_page(destination, origin, index)
        document.save(output)
    return output


class InkBoxTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def test_ink_box_finds_the_drawn_frame(self):
        path = make_document(self.root / "doc.pdf", pages=1)
        with pymupdf.open(path) as document:
            box = ink_box(document[0])
        self.assertIsNotNone(box)
        self.assertAlmostEqual(box.x0, 90, delta=3)
        self.assertAlmostEqual(box.y0, 100, delta=3)
        self.assertAlmostEqual(box.x1, PAGE_WIDTH - 90, delta=3)
        self.assertAlmostEqual(box.y1, PAGE_HEIGHT - 100, delta=3)

    def test_blank_page_has_no_ink_box(self):
        path = self.root / "blank.pdf"
        document = pymupdf.open()
        document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        document.save(path)
        document.close()
        with pymupdf.open(path) as document:
            self.assertIsNone(ink_box(document[0]))


class EstimateAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.origin = make_document(self.root / "origin.pdf")

    def tearDown(self):
        self.folder.cleanup()

    def _estimate(self, variant: Path):
        with pymupdf.open(self.origin) as source, pymupdf.open(variant) as target:
            return estimate_alignment(source, target)

    def test_uniform_scale_only(self):
        variant = relayout(
            self.origin, self.root / "scaled.pdf",
            page_scale=1.0, content_scale=0.8, pad_x=72.0, pad_y=54.0,
        )
        fit = self._estimate(variant)
        self.assertAlmostEqual(fit.scale, 1 / 0.8, delta=0.01)
        self.assertTrue(fit.axes_agree)
        self.assertTrue(fit.improves)
        self.assertLess(points_to_mm(fit.residual), 1.5)

    def test_margins_only(self):
        variant = relayout(
            self.origin, self.root / "margins.pdf",
            page_scale=1.2, content_scale=1.0, pad_x=60.0, pad_y=40.0,
        )
        fit = self._estimate(variant)
        self.assertAlmostEqual(fit.scale, 1.0, delta=0.01)
        self.assertAlmostEqual(fit.offset_x, -60.0, delta=2.0)
        self.assertAlmostEqual(fit.offset_y, -40.0, delta=2.0)
        self.assertTrue(fit.improves)

    def test_scale_and_margins_together(self):
        variant = relayout(
            self.origin, self.root / "both.pdf",
            page_scale=1.25, content_scale=0.75, pad_x=100.0, pad_y=80.0,
        )
        fit = self._estimate(variant)
        self.assertAlmostEqual(fit.scale, 1 / 0.75, delta=0.02)
        self.assertAlmostEqual(fit.offset_x, -100.0 / 0.75, delta=3.0)
        self.assertTrue(fit.improves)

    def test_identical_document_needs_no_correction(self):
        fit = self._estimate(self.origin)
        self.assertTrue(fit.identity)
        self.assertFalse(fit.improves)

    def test_stretched_page_reports_axis_disagreement(self):
        variant = self.root / "stretched.pdf"
        with pymupdf.open(self.origin) as origin, pymupdf.open() as document:
            for index in range(origin.page_count):
                rect = origin[index].rect
                page = document.new_page(width=rect.width, height=rect.height)
                page.show_pdf_page(
                    pymupdf.Rect(0, 0, rect.width * 0.7, rect.height), origin, index,
                    keep_proportion=False,
                )
            document.save(variant)
        fit = self._estimate(variant)
        self.assertFalse(fit.axes_agree)

    def test_blank_documents_cannot_be_aligned(self):
        blank = self.root / "blank.pdf"
        document = pymupdf.open()
        for _ in range(4):
            document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        document.save(blank)
        document.close()
        self.assertIsNone(self._estimate(blank))


class BuildAlignedPdfTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.origin = make_document(self.root / "origin.pdf")

    def tearDown(self):
        self.folder.cleanup()

    def test_rebuilt_pages_match_the_original_geometry_and_look(self):
        variant = relayout(
            self.origin, self.root / "variant.pdf",
            page_scale=1.3, content_scale=0.7, pad_x=90.0, pad_y=70.0,
        )
        output = self.root / "aligned.pdf"
        with pymupdf.open(self.origin) as source, pymupdf.open(variant) as target:
            fit = estimate_alignment(source, target)
            build_aligned_pdf(source, target, fit, output)

        with pymupdf.open(self.origin) as source, pymupdf.open(output) as aligned:
            self.assertEqual(aligned.page_count, source.page_count)
            worst = 0.0
            for index in range(source.page_count):
                self.assertAlmostEqual(aligned[index].rect.width, source[index].rect.width, delta=0.1)
                self.assertAlmostEqual(aligned[index].rect.height, source[index].rect.height, delta=0.1)
                matrix = pymupdf.Matrix(0.5, 0.5)
                left = source[index].get_pixmap(matrix=matrix, colorspace=pymupdf.csGRAY, alpha=False)
                right = aligned[index].get_pixmap(matrix=matrix, colorspace=pymupdf.csGRAY, alpha=False)
                difference = sum(abs(a - b) for a, b in zip(left.samples, right.samples))
                worst = max(worst, difference / len(left.samples))
            # 같은 내용을 다시 앉힌 것이므로 리샘플링 오차만 남아야 한다.
            self.assertLess(worst, 12.0)


if __name__ == "__main__":
    unittest.main()
