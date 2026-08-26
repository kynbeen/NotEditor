from __future__ import annotations

import struct
import unittest
from io import BytesIO

from PIL import Image

from noteditor.sdocx_ink import read_ink_strokes, render_ink_png
from tests.test_sdocx_page import make_page


def _delta(value: float) -> int:
    sign = 0x8000 if value < 0 else 0
    magnitude = abs(value)
    integer = int(magnitude)
    fraction = round((magnitude - integer) * 32)
    return sign | (integer << 5) | fraction


def make_stroke_layers() -> bytes:
    points = [(100.0, 200.0), (110.0, 205.0), (106.0, 212.0)]
    geometry = bytearray(struct.pack("<dd", *points[0]))
    for (left_x, left_y), (right_x, right_y) in zip(points, points[1:]):
        geometry += struct.pack("<HH", _delta(right_x - left_x), _delta(right_y - left_y))
    geometry += struct.pack("<fHH", 1.0, 0, 0)       # pressure
    geometry += struct.pack("<iHH", 0, 1, 1)         # timestamps
    geometry += struct.pack("<H", 0)                 # tail

    prefix_size = 6 + 4 + 1 + 1 + 1 + 2
    subrecord_size = prefix_size + len(geometry)
    subrecord = (
        struct.pack("<IH", subrecord_size, 1)
        + struct.pack("<I", subrecord_size)
        + b"\x01\x01"                                # mask1 length + compact flag
        + b"\x00"                                    # empty mask2
        + struct.pack("<H", len(points))
        + geometry
    )
    object_size = len(subrecord) + 32
    object_record = b"\x01" + struct.pack("<HI", 0, object_size) + subrecord + bytes(32)
    layer_header = bytearray(16)
    struct.pack_into("<I", layer_header, 0, len(layer_header))
    return struct.pack("<HH", 1, 0) + layer_header + struct.pack("<I", 1) + object_record + bytes(32)


class SdocxInkTests(unittest.TestCase):
    def test_reads_compact_stroke_object(self):
        width, height, strokes = read_ink_strokes(make_page(strokes=make_stroke_layers()))
        self.assertEqual((width, height), (1848, 1039))
        self.assertEqual(len(strokes), 1)
        self.assertEqual(strokes[0].points, ((100.0, 200.0), (110.0, 205.0), (106.0, 212.0)))

    def test_renders_transparent_ink_at_preview_size(self):
        png, count = render_ink_png(make_page(strokes=make_stroke_layers()), 924, 520)
        self.assertEqual(count, 1)
        with Image.open(BytesIO(png)) as image:
            self.assertEqual(image.size, (924, 520))
            self.assertEqual(image.mode, "RGBA")
            self.assertIsNotNone(image.getchannel("A").getbbox())


if __name__ == "__main__":
    unittest.main()
