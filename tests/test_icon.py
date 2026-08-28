import tempfile
import unittest
from pathlib import Path

from PIL import Image

from noteditor.app import APP_USER_MODEL_ID
from noteditor.make_icon import build_icon, build_pwa_icons


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
        self.assertEqual(APP_USER_MODEL_ID, "NotEditor.Desktop")

    def test_pwa_icons_include_required_install_sizes(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "icons"
            build_pwa_icons(output_dir)
            for size in (180, 192, 512):
                with Image.open(output_dir / f"icon-{size}.png") as icon:
                    self.assertEqual(icon.size, (size, size))
                    self.assertEqual(icon.mode, "RGBA")
