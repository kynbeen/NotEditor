from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pymupdf

from noteditor.page_match import MatchResult, PagePair
from noteditor.sdocx_note import PageOrder, PageOrderEntry, read_note, read_page_order
from noteditor.sdocx_page import is_blank_page, page_hash, read_page
from noteditor.sdocx_rebuild import rebuild_handwriting
from noteditor.sdocx_transfer import parse_media_info
from tests.test_sdocx_note import make_note
from tests.test_sdocx_ink import make_stroke_layers
from tests.test_sdocx_page import make_page
from tests.test_sdocx_transfer import SPEN_FOOTER, make_media_info, make_pdf, read_footer


UUIDS = [
    "10000000-0000-0000-0000-000000000000",
    "20000000-0000-0000-0000-000000000000",
    "30000000-0000-0000-0000-000000000000",
    "40000000-0000-0000-0000-000000000000",
    "50000000-0000-0000-0000-000000000000",
]
NEW_UUID = "99999999-9999-9999-9999-999999999999"


def layer_payload(object_count: int) -> bytes:
    header = bytearray(98)
    header[:4] = len(header).to_bytes(4, "little")
    # 비어 있지 않은 쪽은 개수만 확인하면 되므로 합성 객체 본문까지 만들 필요가 없다.
    return b"\x01\x00\x00\x00" + bytes(header) + object_count.to_bytes(4, "little") + bytes(32)


def make_rebuild_source(
    path: Path, embedded_pdf: Path, *, annotated_layers: bytes | None = None
) -> dict[str, bytes]:
    pages = [
        make_page(uuid=UUIDS[0], pdf_page_index=0, strokes=layer_payload(0), hash_block=b"A" * 32),
        make_page(uuid=UUIDS[1], pdf_page_index=1, strokes=layer_payload(0), hash_block=b"B" * 32),
        make_page(
            uuid=UUIDS[2],
            pdf_page_index=2,
            strokes=annotated_layers or layer_payload(1),
            hash_block=b"C" * 32,
        ),
        make_page(uuid=UUIDS[3], pdf_page_index=3, strokes=layer_payload(0), hash_block=b"D" * 32),
        make_page(
            uuid=UUIDS[4],
            canvas=(1848, 2613),
            mask=0x60,
            strokes=layer_payload(0),
            hash_block=b"E" * 32,
        ),
    ]
    order = PageOrder(
        file_hash=b"F" * 32,
        entries=tuple(PageOrderEntry(uuid, page_hash(blob)) for uuid, blob in zip(UUIDS, pages)),
    )
    pdf_bytes = embedded_pdf.read_bytes()
    payloads = {
        "note.note": make_note(height=99999),
        "pageIdInfo.dat": order.to_bytes(),
        **{f"{uuid}.page": blob for uuid, blob in zip(UUIDS, pages)},
        "media/0@source.pdf": pdf_bytes,
        "media/mediaInfo.dat": make_media_info("0@source.pdf", pdf_bytes),
        "end_tag.bin": SPEN_FOOTER,
    }
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, payload in payloads.items():
            archive.writestr(name, payload)
    with path.open("ab") as handle:
        handle.write(SPEN_FOOTER)
    return payloads


class RebuildHandwritingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.source_pdf = self.root / "source.pdf"
        self.target_pdf = self.root / "target.pdf"
        self.source_sdocx = self.root / "source.sdocx"
        make_pdf(self.source_pdf, ["A", "DROP", "KEEP", "C"])
        make_pdf(self.target_pdf, ["A", "NEW", "C"])
        self.payloads = make_rebuild_source(self.source_sdocx, self.source_pdf)

    def tearDown(self):
        self.folder.cleanup()

    def test_rebuild_keeps_annotated_source_page_and_all_target_pages(self):
        match = MatchResult(
            pairs=(
                PagePair(0, 0, 0.0, 1.0),
                PagePair(1, None),
                PagePair(2, None),
                PagePair(None, 1),
                PagePair(3, 2, 0.0, 1.0),
            )
        )
        output = self.root / "rebuilt.sdocx"
        result = rebuild_handwriting(
            self.source_sdocx,
            self.target_pdf,
            output,
            match,
            uuid_factory=lambda: NEW_UUID,
            hash_factory=lambda size: b"N" * size,
        )

        self.assertEqual(result["page_count"], 4)
        self.assertEqual(result["note_page_count"], 5)
        self.assertEqual(result["preserved_source_only_count"], 1)
        self.assertEqual(result["dropped_blank_count"], 1)
        self.assertEqual(read_footer(output), SPEN_FOOTER)

        with ZipFile(output) as archive:
            names = archive.namelist()
            self.assertNotIn(f"{UUIDS[1]}.page", names)
            self.assertIn(f"{NEW_UUID}.page", names)
            order = read_page_order(archive.read("pageIdInfo.dat"))
            self.assertEqual(
                [entry.uuid for entry in order.entries],
                [UUIDS[0], UUIDS[2], NEW_UUID, UUIDS[3], UUIDS[4]],
            )
            for entry in order.entries:
                blob = archive.read(f"{entry.uuid}.page")
                self.assertEqual(page_hash(blob), entry.page_hash)

            kept = read_page(archive.read(f"{UUIDS[2]}.page"))
            added = read_page(archive.read(f"{NEW_UUID}.page"))
            supplemental = archive.read(f"{UUIDS[4]}.page")
            self.assertEqual(kept.pdf.page_index, 1)
            self.assertEqual(added.pdf.page_index, 2)
            self.assertEqual(added.page_hash, b"N" * 32)
            self.assertTrue(is_blank_page(archive.read(f"{NEW_UUID}.page")))
            self.assertEqual(supplemental, self.payloads[f"{UUIDS[4]}.page"])
            self.assertEqual(
                read_note(archive.read("note.note")).height,
                1039 * 4 + 2613 + 41 * 4,
            )

            embedded = archive.read("media/0@source.pdf")
            media = parse_media_info(archive.read("media/mediaInfo.dat"))[0]
            self.assertEqual(media.file_hash, hashlib.sha256(embedded).hexdigest())

        with pymupdf.open(stream=embedded, filetype="pdf") as document:
            self.assertEqual(document.page_count, 4)
            labels = [document[index].get_text().strip() for index in range(document.page_count)]
            self.assertEqual(labels, ["A", "KEEP", "NEW", "C"])

    def test_rebuild_accepts_confirmed_target_reorder_and_preserves_source_only_ink(self):
        match = MatchResult(
            pairs=(
                PagePair(3, 2),
                PagePair(0, 0),
                PagePair(1, None),
                PagePair(2, None),
                PagePair(None, 1),
            )
        )
        output = self.root / "reordered.sdocx"
        result = rebuild_handwriting(
            self.source_sdocx,
            self.target_pdf,
            output,
            match,
            uuid_factory=lambda: NEW_UUID,
            hash_factory=lambda size: b"N" * size,
        )

        self.assertEqual(result["page_count"], 4)
        self.assertEqual(result["preserved_source_only_count"], 1)
        self.assertEqual(result["dropped_blank_count"], 1)
        with ZipFile(output) as archive:
            order = read_page_order(archive.read("pageIdInfo.dat"))
            self.assertEqual(
                [entry.uuid for entry in order.entries],
                [UUIDS[3], UUIDS[0], UUIDS[2], NEW_UUID, UUIDS[4]],
            )
            self.assertEqual(read_page(archive.read(f"{UUIDS[3]}.page")).pdf.page_index, 0)
            self.assertEqual(read_page(archive.read(f"{UUIDS[0]}.page")).pdf.page_index, 1)
            self.assertEqual(read_page(archive.read(f"{UUIDS[2]}.page")).pdf.page_index, 2)
            self.assertEqual(read_page(archive.read(f"{NEW_UUID}.page")).pdf.page_index, 3)
            embedded = archive.read("media/0@source.pdf")

        with pymupdf.open(stream=embedded, filetype="pdf") as document:
            labels = [document[index].get_text().strip() for index in range(document.page_count)]
            self.assertEqual(labels, ["C", "A", "KEEP", "NEW"])

    def test_different_aspect_ratio_uses_target_page_and_moves_editable_ink(self):
        source_pdf = self.root / "wide-source.pdf"
        target_pdf = self.root / "tall-target.pdf"
        source_sdocx = self.root / "wide-source.sdocx"
        make_pdf(source_pdf, ["A", "B", "C", "D"], width=960, height=540)
        make_rebuild_source(
            source_sdocx, source_pdf, annotated_layers=make_stroke_layers()
        )
        with pymupdf.open(source_pdf) as origin, pymupdf.open() as target:
            for index in range(origin.page_count):
                page = target.new_page(width=960, height=720)
                page.show_pdf_page(
                    pymupdf.Rect(0, 90, 960, 630), origin, index
                )
            target.save(target_pdf)

        match = MatchResult(
            tuple(PagePair(index, index) for index in range(4))
        )
        output = self.root / "target-ratio.sdocx"
        result = rebuild_handwriting(source_sdocx, target_pdf, output, match)
        self.assertIsNotNone(result["alignment"])

        with ZipFile(output) as archive:
            embedded = archive.read("media/0@source.pdf")
            transformed = read_page(archive.read(f"{UUIDS[2]}.page"))
            from noteditor.sdocx_ink import read_ink_strokes

            _width, _height, strokes = read_ink_strokes(
                archive.read(f"{UUIDS[2]}.page")
            )
        with pymupdf.open(stream=embedded, filetype="pdf") as document:
            self.assertTrue(all(page.rect == pymupdf.Rect(0, 0, 960, 720) for page in document))
        self.assertAlmostEqual(
            transformed.canvas_width / transformed.canvas_height,
            960 / 720,
            delta=0.002,
        )
        self.assertEqual(
            tuple(round(value) for value in transformed.pdf.rect),
            (0, 0, transformed.canvas_width, transformed.canvas_height),
        )
        self.assertEqual(len(strokes), 1)
        self.assertGreater(strokes[0].points[0][1], 300.0)


if __name__ == "__main__":
    unittest.main()
