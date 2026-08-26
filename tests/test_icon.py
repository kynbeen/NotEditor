import tempfile
import unittest
from pathlib import Path

from PIL import Image

from pdf_page_composer.app import APP_USER_MODEL_ID
from pdf_page_composer.make_icon import build_icon


class DesktopIconTests(unittest.TestCase):
    def test_icon_has_transparent_canvas_and_visible_mark(self):
        with tempfile.TemporaryDirectory() as temporary:
            icon_path = Path(temporary) / "icon.ico"
            build_icon(icon_path)
            with Image.open(icon_path) as icon:
                image = icon.convert("RGBA")
        self.assertEqual(image.getpixel((0, 0))[3], 0)
        self.assertGreater(image.getpixel((image.width // 2, image.height // 2))[3], 0)

    def test_windows_app_identity_is_stable(self):
        self.assertEqual(APP_USER_MODEL_ID, "PDFPageComposer.Desktop")
