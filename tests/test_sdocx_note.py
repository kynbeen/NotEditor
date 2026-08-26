from __future__ import annotations

import struct
import unittest

from noteditor.sdocx_note import (
    PageOrder,
    PageOrderEntry,
    SdocxNoteError,
    note_height,
    patch_note_height,
    read_note,
    read_page_order,
)

UUIDS = [
    "6bc445ec-9f4c-11f1-81f5-47c391374734",
    "6bc4484e-9f4c-11f1-a787-c394c809874d",
    "6bc44998-9f4c-11f1-b020-2397331dfc30",
]


def make_order(uuids: list[str]) -> bytes:
    payload = bytearray(bytes(range(32)))
    payload += struct.pack("<H", len(uuids))
    for index, uuid in enumerate(uuids):
        payload += struct.pack("<H", len(uuid))
        payload += uuid.encode("utf-16le")
        payload += bytes([index + 1]) * 32
    return bytes(payload)


def make_note(*, height: int = 56613, pad_y: int = 41, width: int = 1848) -> bytes:
    blob = bytearray(b"\x00" * 0x0E)
    blob += struct.pack("<I", 4000)                    # formatVersion
    blob += struct.pack("<H", 0)                       # noteId (빈 문자열)
    blob += struct.pack("<I", 5)                       # fileRevision
    blob += struct.pack("<Q", 1787505000000)           # createdTime
    blob += struct.pack("<Q", 1787528599896)           # modifiedTime
    blob += struct.pack("<IIII", width, height, 0, pad_y)
    blob += struct.pack("<I", 4000)                    # minFormatVersion
    blob += b"title-and-body-payload"
    return bytes(blob)


class PageOrderTests(unittest.TestCase):
    def test_round_trip_preserves_bytes(self):
        blob = make_order(UUIDS)
        order = read_page_order(blob)
        self.assertEqual([entry.uuid for entry in order.entries], UUIDS)
        self.assertEqual(order.entries[1].page_hash, bytes([2]) * 32)
        self.assertEqual(order.to_bytes(), blob)

    def test_pages_can_be_reordered_added_and_removed(self):
        order = read_page_order(make_order(UUIDS))
        rebuilt = PageOrder(
            file_hash=order.file_hash,
            entries=(
                order.entries[2],
                order.entries[0],
                PageOrderEntry("00000000-1111-2222-3333-444444444444", b"\x09" * 32),
            ),
        )
        again = read_page_order(rebuilt.to_bytes())
        self.assertEqual(
            [entry.uuid for entry in again.entries],
            [UUIDS[2], UUIDS[0], "00000000-1111-2222-3333-444444444444"],
        )
        self.assertEqual(again.entries[0].page_hash, bytes([3]) * 32)

    def test_rejects_leftover_bytes(self):
        with self.assertRaises(SdocxNoteError):
            read_page_order(make_order(UUIDS) + b"\x00\x00")

    def test_rejects_a_truncated_entry(self):
        with self.assertRaises(SdocxNoteError):
            read_page_order(make_order(UUIDS)[:-10])

    def test_rejects_a_hash_of_the_wrong_size(self):
        order = read_page_order(make_order(UUIDS))
        broken = PageOrder(order.file_hash, (PageOrderEntry(UUIDS[0], b"\x01" * 16),))
        with self.assertRaises(SdocxNoteError):
            broken.to_bytes()


class NoteMetadataTests(unittest.TestCase):
    def test_reads_the_size_fields(self):
        info = read_note(make_note())
        self.assertEqual(info.format_version, 4000)
        self.assertEqual(info.width, 1848)
        self.assertEqual(info.height, 56613)
        self.assertEqual(info.vertical_padding, 41)

    def test_height_formula_matches_the_observed_files(self):
        # 실제 파일: 50쪽 × 1039 + 1쪽 × 2613, 세로여백 41 → 56613
        heights = [1039] * 50 + [2613]
        self.assertEqual(note_height(heights, 41), 56613)

    def test_patching_height_keeps_every_other_byte(self):
        blob = make_note()
        heights = [1039] * 10
        patched = patch_note_height(blob, heights)
        self.assertEqual(len(patched), len(blob))
        self.assertEqual(read_note(patched).height, 1039 * 10 + 41 * 9)
        info = read_note(blob)
        self.assertEqual(patched[:info.height_offset], blob[:info.height_offset])
        self.assertEqual(patched[info.height_offset + 4:], blob[info.height_offset + 4:])

    def test_rewriting_the_same_page_set_is_a_no_op(self):
        blob = make_note(height=note_height([1039] * 3, 41))
        self.assertEqual(patch_note_height(blob, [1039] * 3), blob)

    def test_refuses_a_note_with_no_pages(self):
        with self.assertRaises(SdocxNoteError):
            patch_note_height(make_note(), [])


if __name__ == "__main__":
    unittest.main()
