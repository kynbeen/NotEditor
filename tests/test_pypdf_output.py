from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pymupdf
from PIL import ImageChops, ImageStat
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import RectangleObject

from noteditor.pypdf_output import OutputDocument, PageRect
from noteditor import pdf as pdf_backend


def make_source(path: Path) -> Path:
    with pymupdf.open() as document:
        first = document.new_page(width=200, height=100)
        first.draw_rect(first.rect, fill=(0.1, 0.4, 0.8), overlay=False)
        first.insert_text((20, 55), "FIRST", fontsize=24, color=(1, 1, 1))
        second = document.new_page(width=120, height=180)
        second.insert_text((20, 60), "SECOND", fontsize=20)
        document.save(path)
    return path


class PypdfOutputTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.source = make_source(self.root / "source.pdf")

    def tearDown(self):
        self.folder.cleanup()

    def test_insert_pdf_keeps_requested_page_order(self):
        output = OutputDocument()
        output.insert_pdf(self.source, from_page=1, to_page=1)
        output.insert_pdf(self.source, from_page=0, to_page=0)
        result = self.root / "reordered.pdf"
        output.save(result)

        with pymupdf.open(result) as document:
            self.assertEqual(document.page_count, 2)
            self.assertEqual([page.get_text().strip() for page in document], ["SECOND", "FIRST"])
            self.assertEqual((document[0].rect.width, document[0].rect.height), (120, 180))

    def test_show_pdf_page_uses_mupdf_top_left_coordinates(self):
        output = OutputDocument()
        page = output.new_page(width=300, height=300)
        page.show_pdf_page(PageRect(50, 50, 250, 250), self.source, 0)
        result = self.root / "placed.pdf"
        output.save(result)

        with pymupdf.open(result) as document:
            pixmap = document[0].get_pixmap(alpha=False)
            image = pixmap.pil_image()
            self.assertEqual(image.getpixel((150, 75)), (255, 255, 255))
            self.assertNotEqual(image.getpixel((150, 150)), (255, 255, 255))
            self.assertEqual(image.getpixel((150, 225)), (255, 255, 255))

    def test_output_round_trips_through_bytes(self):
        output = OutputDocument()
        output.new_page(width=360, height=480)
        payload = output.tobytes()

        with pymupdf.open(stream=payload, filetype="pdf") as document:
            self.assertEqual(document.page_count, 1)
            self.assertEqual((document[0].rect.width, document[0].rect.height), (360, 480))

    def test_invalid_insert_range_is_rejected(self):
        output = OutputDocument()
        with self.assertRaises(IndexError):
            output.insert_pdf(self.source, from_page=1, to_page=2)

    def shifted_source(self, rotation=0, crop=True):
        reader = PdfReader(self.source)
        writer = PdfWriter()
        page = writer.add_page(reader.pages[0])
        page.add_transformation(Transformation().translate(40, 25))
        page.mediabox = RectangleObject((40, 25, 240, 125))
        page.cropbox = RectangleObject((60, 35, 220, 115) if crop else (40, 25, 240, 125))
        page.rotate(rotation)
        path = self.root / f"offset-{rotation}-{crop}.pdf"
        writer.write(path)
        return path

    def test_cropped_offset_and_rotated_sources_match_desktop_placement(self):
        destination = PageRect(25, 35, 275, 265)
        for crop in (False, True):
            for rotation in (0, 90, 180, 270):
                with self.subTest(crop=crop, rotation=rotation):
                    source = self.shifted_source(rotation, crop)
                    original = source.read_bytes()
                    output = OutputDocument()
                    page = output.new_page(width=300, height=300)
                    page.show_pdf_page(destination, source, 0)
                    with pymupdf.open(source) as native_source, pymupdf.open() as expected:
                        pdf_backend.show_pdf_page(expected.new_page(width=300, height=300),
                                                  pymupdf.Rect(*destination), native_source, 0)
                        expected_image = expected[0].get_pixmap(alpha=False).pil_image()
                    with pymupdf.open(stream=output.tobytes(), filetype="pdf") as result:
                        actual_image = result[0].get_pixmap(alpha=False).pil_image()
                    difference = ImageStat.Stat(ImageChops.difference(actual_image, expected_image))
                    self.assertLess(max(difference.mean), 1.0)
                    self.assertEqual(source.read_bytes(), original)

    def test_inserted_page_rect_matches_visible_crop_and_rotation(self):
        source = self.shifted_source(rotation=90)
        output = OutputDocument()
        output.insert_pdf(source)
        with pymupdf.open(source) as native:
            self.assertEqual(tuple(output[0].rect), tuple(native[0].rect))

    def test_both_placement_paths_preserve_the_actual_displayed_page(self):
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                source = self.shifted_source(rotation)
                with pymupdf.open(source) as native_source, pymupdf.open() as desktop:
                    source_page = native_source[0]
                    rect = source_page.rect
                    original_box = tuple(source_page.cropbox)
                    original_image = source_page.get_pixmap(alpha=False).pil_image()
                    pdf_backend.show_pdf_page(desktop.new_page(width=rect.width, height=rect.height),
                                              rect, native_source, 0)
                    android = OutputDocument()
                    android.new_page(width=rect.width, height=rect.height).show_pdf_page(
                        PageRect(*rect), source, 0
                    )
                    with pymupdf.open(stream=android.tobytes(), filetype="pdf") as assembled:
                        for output in (desktop, assembled):
                            rendered = output[0].get_pixmap(alpha=False).pil_image()
                            difference = ImageStat.Stat(ImageChops.difference(rendered, original_image))
                            self.assertLess(max(difference.mean), 1.0)
                    self.assertEqual(source_page.rotation, rotation)
                    self.assertEqual(tuple(source_page.cropbox), original_box)

    def test_nonzero_destination_origin_keeps_top_left_placement(self):
        output = OutputDocument()
        page = output.new_page(width=300, height=300)
        page.set_mediabox(PageRect(40, 25, 340, 325))
        page.show_pdf_page(PageRect(50, 100, 250, 200), self.source, 0)
        with pymupdf.open(stream=output.tobytes(), filetype="pdf") as document:
            image = document[0].get_pixmap(alpha=False).pil_image()
            self.assertEqual(image.getpixel((150, 75)), (255, 255, 255))
            self.assertNotEqual(image.getpixel((150, 150)), (255, 255, 255))
            self.assertEqual(image.getpixel((150, 225)), (255, 255, 255))

    def test_mediabox_change_resets_previous_crop(self):
        source = self.shifted_source()
        output = OutputDocument()
        output.insert_pdf(source)
        output[0].set_mediabox(PageRect(0, 0, 300, 300))
        with pymupdf.open(stream=output.tobytes(), filetype="pdf") as result:
            self.assertEqual(tuple(result[0].rect), (0, 0, 300, 300))


if __name__ == "__main__":
    unittest.main()
