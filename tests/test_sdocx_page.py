from __future__ import annotations

import struct
import unittest

from noteditor.sdocx_page import (
    PAGE_FOOTER,
    SdocxPageError,
    is_blank_page,
    page_hash,
    patch_page,
    read_page,
)

UUID = "6bc445ec-9f4c-11f1-81f5-47c391374734"
MASK_PDF_BACKGROUND = 0x160  # bgColor | bgWidth | pdfDataList


def make_page(
    *,
    uuid: str = UUID,
    canvas: tuple[int, int] = (1848, 1039),
    pdf_page_index: int = 3,
    file_id: int = 0,
    mask: int = MASK_PDF_BACKGROUND,
    strokes: bytes = b"\x11" * 40,
    hash_block: bytes | None = None,
    layer_offset: int | None = None,
) -> bytes:
    """Samsung이 쓰는 것과 같은 배치의 최소 ``.page``."""
    metadata = bytearray()
    metadata += struct.pack("<I", 0)                      # noteOrientation
    metadata += struct.pack("<II", *canvas)               # pageWidth, pageHeight
    metadata += struct.pack("<ii", 0, 0)                  # offsetX, offsetY
    metadata += struct.pack("<H", len(uuid)) + uuid.encode("utf-16le")
    metadata += struct.pack("<Q", 1787528599896)          # modifiedTime
    metadata += struct.pack("<II", 2040, 2000)            # formatVersion, minFormatVersion

    properties = bytearray()
    if mask & 0x20:
        properties += struct.pack("<I", 0xFFFCFCFC)       # bgColor
    if mask & 0x40:
        properties += struct.pack("<I", canvas[0])        # bgWidth
    if mask & 0x100:
        properties += struct.pack("<H", 1)                # pdfDataList count
        properties += struct.pack("<ii", file_id, pdf_page_index)
        properties += struct.pack("<iiii", 0, 0, canvas[0], canvas[1])

    property_offset = 0x12 + len(metadata)
    computed_layer = property_offset + len(properties)
    header = bytearray(0x12)
    struct.pack_into("<I", header, 0x00, computed_layer if layer_offset is None else layer_offset)
    struct.pack_into("<I", header, 0x04, property_offset)
    header[0x08] = 0x04
    struct.pack_into("<I", header, 0x09, 0)
    header[0x0D] = 0x04
    struct.pack_into("<I", header, 0x0E, mask)

    tail = (hash_block or bytes(range(32))) + PAGE_FOOTER
    return bytes(header) + bytes(metadata) + bytes(properties) + strokes + tail


class ReadPageTests(unittest.TestCase):
    def test_reads_metadata_and_pdf_reference(self):
        info = read_page(make_page())
        self.assertEqual(info.uuid, UUID)
        self.assertEqual((info.canvas_width, info.canvas_height), (1848, 1039))
        self.assertEqual(info.property_mask, MASK_PDF_BACKGROUND)
        self.assertIsNotNone(info.pdf)
        self.assertEqual(info.pdf.page_index, 3)
        self.assertEqual(info.pdf.file_id, 0)
        self.assertEqual(info.pdf.entry_count, 1)
        self.assertTrue(info.has_pdf_background)

    def test_property_offset_matches_samsung_layout(self):
        # UUID 36자 기준으로 속성 블록은 0x80에서 시작한다 (역공학 문서의 관측값과 같다).
        self.assertEqual(read_page(make_page()).property_offset, 0x80)

    def test_page_hash_is_the_block_before_the_footer(self):
        blob = make_page(hash_block=bytes(range(100, 132)))
        self.assertEqual(page_hash(blob), bytes(range(100, 132)))
        self.assertEqual(read_page(blob).page_hash, bytes(range(100, 132)))

    def test_rejects_a_page_without_the_samsung_footer(self):
        with self.assertRaises(SdocxPageError):
            read_page(make_page()[:-4])

    def test_rejects_unknown_property_bits(self):
        with self.assertRaises(SdocxPageError):
            read_page(make_page(mask=MASK_PDF_BACKGROUND | 0x00100000))

    def test_rejects_a_property_walk_that_misses_the_layer_section(self):
        # 속성 블록의 끝이 레이어 시작과 안 맞으면 해석이 틀린 것이므로 거부해야 한다.
        with self.assertRaises(SdocxPageError):
            read_page(make_page(layer_offset=0x99))

    def test_blank_page_is_decided_by_layer_object_counts(self):
        layer_header = bytearray(98)
        struct.pack_into("<I", layer_header, 0, len(layer_header))
        empty_layers = struct.pack("<HH", 1, 0) + layer_header + struct.pack("<I", 0) + bytes(32)
        used_layers = struct.pack("<HH", 1, 0) + layer_header + struct.pack("<I", 1)
        self.assertTrue(is_blank_page(make_page(strokes=empty_layers)))
        self.assertFalse(is_blank_page(make_page(strokes=used_layers)))


class PatchPageTests(unittest.TestCase):
    def test_changes_only_the_requested_fixed_size_fields(self):
        blob = make_page()
        other = "00000000-1111-2222-3333-444444444444"
        patched = patch_page(
            blob,
            pdf_page_index=41,
            uuid=other,
            canvas=(1848, 2000),
            new_page_hash=bytes(range(32, 64)),
        )
        self.assertEqual(len(patched), len(blob))
        info = read_page(patched)
        self.assertEqual(info.pdf.page_index, 41)
        self.assertEqual(info.uuid, other)
        self.assertEqual((info.canvas_width, info.canvas_height), (1848, 2000))
        self.assertEqual(info.page_hash, bytes(range(32, 64)))
        # 획 구간은 그대로여야 한다.
        self.assertEqual(patched[info.layer_offset:-58], blob[info.layer_offset:-58])

    def test_round_trip_restores_the_original_bytes(self):
        blob = make_page()
        info = read_page(blob)
        changed = patch_page(blob, pdf_page_index=7, canvas=(10, 20))
        restored = patch_page(
            changed,
            pdf_page_index=info.pdf.page_index,
            canvas=(info.canvas_width, info.canvas_height),
        )
        self.assertEqual(restored, blob)

    def test_refuses_a_uuid_of_a_different_length(self):
        with self.assertRaises(SdocxPageError):
            patch_page(make_page(), uuid="too-short")

    def test_refuses_to_point_a_page_without_pdf_background(self):
        with self.assertRaises(SdocxPageError):
            patch_page(make_page(mask=0x60), pdf_page_index=2)

    def test_refuses_a_hash_block_of_the_wrong_size(self):
        with self.assertRaises(SdocxPageError):
            patch_page(make_page(), new_page_hash=b"\x00" * 16)


if __name__ == "__main__":
    unittest.main()
