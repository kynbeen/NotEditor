from __future__ import annotations

import base64
import io
import struct
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

import pymupdf
from PIL import Image

from noteditor.alignment import ink_box
from noteditor.notewise_ink import render_notewise_ink
from noteditor.notewise_proto import NotewiseTransferError, iter_fields
from noteditor.notewise_transfer import (
    _page_ids,
    _validate_mapping,
    inspect_notewise_transfer,
    transfer_notewise_handwriting,
)


def _varint(value: int) -> bytes:
    output = bytearray()
    while value >= 0x80:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def _field(number: int, payload: bytes) -> bytes:
    return _varint((number << 3) | 2) + _varint(len(payload)) + payload


def _number(number: int, value: int) -> bytes:
    return _varint(number << 3) + _varint(value)


def _make_pdf(path: Path, label: str, pages: int = 1) -> None:
    with pymupdf.open() as document:
        for index in range(pages):
            page = document.new_page(width=300, height=400)
            page.insert_text((30, 50), label if index == 0 else f"{label} p{index + 1}")
        document.save(path)


def _page_id(index: int) -> bytes:
    """첫 쪽 이름은 기존 테스트가 문자열로 참조하므로 그대로 둔다."""
    return b"page-id" if index == 0 else f"page-id-{index}".encode()


def _make_notewise(path: Path, pdf: Path, annotated: bool = True, pages: int = 1) -> None:
    pdf_id = b"pdf-id"
    relation_id = b"relation-id"
    dimensions = _number(3, 300) + _number(4, 400)
    page_ids = [_page_id(index) for index in range(pages)]
    payloads = []
    for index, page_id in enumerate(page_ids):
        page = _field(1, page_id)
        if index > 0:
            page += _number(2, index)
        page += _field(3, _field(1, pdf_id) + _number(2, index))
        if annotated:
            style = _field(1, b"#000000") + _varint((2 << 3) | 5) + struct.pack("<f", 1.0)
            pen = (
                _number(1, 2)
                + _field(3, style)
                + _field(4, struct.pack("<2f", 20.0, 100.0))
                + _field(5, struct.pack("<2f", 20.0, 100.0))
                + _field(6, struct.pack("<2f", 3.0, 3.0))
            )
            page += _field(4, _field(4, pen))
        page += (
            _field(6, _field(1, dimensions))
            + _varint((7 << 3) | 1)
            + struct.pack("<d", 1024.0 * (index + 1))
            + _field(11, relation_id)
        )
        payloads.append((page_id.decode(), page))
    pdf_metadata = _field(1, pdf_id) + _number(4, pages) + _field(5, pdf.name.encode())
    relation = _field(1, relation_id) + _field(2, b"source")
    note = (
        _field(1, b"note-id")
        + _field(2, b"source")
        + b"".join(_field(4, page_id) for page_id in page_ids)
        + _field(6, pdf_metadata)
        + _field(11, relation)
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("note", base64.b64encode(note))
        for name, payload in payloads:
            archive.writestr(f"page/{name}", base64.b64encode(payload))
        archive.writestr("pdf/pdf-id", pdf.read_bytes())


class NotewiseTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_pdf = self.root / "source.pdf"
        self.target_pdf = self.root / "target.pdf"
        self.source = self.root / "source.notewise"
        _make_pdf(self.source_pdf, "same text")
        _make_pdf(self.target_pdf, "same text")
        _make_notewise(self.source, self.source_pdf)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_inspection_counts_pages_and_strokes(self):
        result = inspect_notewise_transfer(self.source, self.target_pdf)
        self.assertEqual(result.page_count, 1)
        self.assertEqual(result.annotated_page_count, 1)
        self.assertEqual(result.stroke_cache_count, 1)
        self.assertEqual(result.mode, "exact")

    def test_transfer_replaces_only_embedded_pdf_payload(self):
        output = self.root / "result.notewise"
        with ZipFile(self.source) as archive:
            before = archive.read("page/page-id")
            before_note_id = next(
                value for number, wire, value
                in iter_fields(base64.b64decode(archive.read("note")))
                if number == 1 and wire == 2
            )
        result = transfer_notewise_handwriting(self.source, self.target_pdf, output)
        with ZipFile(output) as archive:
            pdf_name = next(name for name in archive.namelist() if name.startswith("pdf/"))
            self.assertNotEqual(pdf_name, "pdf/pdf-id")
            self.assertEqual(archive.read(pdf_name), self.target_pdf.read_bytes())
            output_page_id = _page_ids(archive.read("note"))[0]
            self.assertNotEqual(output_page_id, "page-id")
            before_objects = [
                value for number, wire, value
                in iter_fields(base64.b64decode(before))
                if number == 4 and wire == 2
            ]
            after_objects = [
                value for number, wire, value
                in iter_fields(base64.b64decode(archive.read(f"page/{output_page_id}")))
                if number == 4 and wire == 2
            ]
            self.assertEqual(after_objects, before_objects)
            after_note_id = next(
                value for number, wire, value
                in iter_fields(base64.b64decode(archive.read("note")))
                if number == 1 and wire == 2
            )
            self.assertNotEqual(after_note_id, before_note_id)
            self.assertTrue(archive.read("note").endswith(b"\n"))
            self.assertTrue(all(
                len(line) <= 76 for line in archive.read("note").splitlines()
            ))
        self.assertEqual(result["stroke_count"], 1)

    def test_rendered_ink_has_visible_pixels(self):
        with ZipFile(self.source) as archive:
            png, count = render_notewise_ink(archive.read("page/page-id"), (300, 400))
        with Image.open(io.BytesIO(png)) as image:
            self.assertIsNotNone(image.getbbox())
        self.assertEqual(count, 1)

    def test_rejects_overwriting_source(self):
        with self.assertRaises(NotewiseTransferError):
            transfer_notewise_handwriting(self.source, self.target_pdf, self.source)

    def test_rebuild_inserts_blank_page_and_moves_ink(self):
        changed = self.root / "changed.pdf"
        with pymupdf.open() as document:
            first = document.new_page(width=300, height=400)
            first.insert_text((30, 50), "new cover")
            second = document.new_page(width=300, height=400)
            second.insert_text((30, 50), "same text")
            document.save(changed)

        output = self.root / "rebuilt.notewise"
        result = transfer_notewise_handwriting(self.source, changed, output)
        self.assertEqual(result["mode"], "rebuild")
        self.assertEqual(result["new_page_count"], 1)
        with ZipFile(output) as archive:
            page_ids = _page_ids(archive.read("note"))
            self.assertEqual(len(page_ids), 2)
            self.assertNotEqual(page_ids[0], "page-id")
            self.assertNotEqual(page_ids[1], "page-id")
            first_page = base64.b64decode(archive.read(f"page/{page_ids[0]}"))
            second_page = base64.b64decode(archive.read(f"page/{page_ids[1]}"))
            self.assertFalse(any(number == 4 for number, _wire, _value in iter_fields(first_page)))
            self.assertTrue(any(number == 4 for number, _wire, _value in iter_fields(second_page)))
            page_fields = [list(iter_fields(page)) for page in (first_page, second_page)]
            page_orders = [
                next((value for number, wire, value in fields
                      if number == 2 and wire == 0), 0)
                for fields in page_fields
            ]
            sort_keys = [
                struct.unpack(
                    "<d",
                    next(value for number, wire, value in fields
                         if number == 7 and wire == 1),
                )[0]
                for fields in page_fields
            ]
            self.assertEqual(page_orders, [0, 1])
            self.assertEqual(sort_keys, [1024.0, 2048.0])
            background = next(value for number, wire, value in iter_fields(second_page)
                              if number == 3 and wire == 2)
            page_index = next(value for number, wire, value in iter_fields(background)
                              if number == 2 and wire == 0)
            self.assertEqual(page_index, 1)

    def _multipage_source(self) -> tuple[Path, Path]:
        """본문 정렬 추정은 짝지어진 쪽이 2개 이상이어야 성립한다(``_MIN_SAMPLES``)."""
        pdf = self.root / "multi.pdf"
        notewise = self.root / "multi.notewise"
        _make_pdf(pdf, "same text", pages=2)
        _make_notewise(notewise, pdf, pages=2)
        return notewise, pdf

    def _relaid_out(self, origin_pdf: Path, path: Path, extra_cover: bool = False) -> Path:
        """같은 본문을 더 큰 쪽에 축소해 앉힌 PDF. 배율·여백이 달라져 정렬이 필요하다."""
        with pymupdf.open(origin_pdf) as origin, pymupdf.open() as document:
            if extra_cover:
                cover = document.new_page(width=360, height=480)
                cover.insert_text((30, 50), "brand new cover")
            for index in range(origin.page_count):
                rect = origin[index].rect
                page = document.new_page(width=rect.width * 1.2, height=rect.height * 1.2)
                page.show_pdf_page(
                    pymupdf.Rect(30, 20, 30 + rect.width * 0.9, 20 + rect.height * 0.9),
                    origin,
                    index,
                )
            document.save(path)
        return path

    def _assert_canvas_preserved(self, embedded: bytes, origin_pdf: Path, page_count: int) -> None:
        with pymupdf.open(stream=embedded, filetype="pdf") as background, \
                pymupdf.open(origin_pdf) as origin:
            self.assertEqual(background.page_count, page_count)
            for index in range(background.page_count):
                self.assertAlmostEqual(
                    background[index].rect.width, origin[0].rect.width, delta=0.5
                )
                self.assertAlmostEqual(
                    background[index].rect.height, origin[0].rect.height, delta=0.5
                )

    def test_transfer_aligns_a_relaid_out_pdf_to_the_original_canvas(self):
        source, origin_pdf = self._multipage_source()
        variant = self._relaid_out(origin_pdf, self.root / "variant.pdf")

        inspection = inspect_notewise_transfer(source, variant)
        self.assertEqual(inspection.mode, "aligned")
        self.assertIsNotNone(inspection.alignment)
        self.assertAlmostEqual(inspection.alignment.scale, 1 / 0.9, delta=0.05)

        output = self.root / "aligned.notewise"
        result = transfer_notewise_handwriting(source, variant, output)
        self.assertEqual(result["mode"], "aligned")

        with ZipFile(source) as origin_archive:
            before = [
                value for number, wire, value
                in iter_fields(base64.b64decode(origin_archive.read("page/page-id")))
                if number == 4 and wire == 2
            ]
        with ZipFile(output) as archive:
            pdf_name = next(name for name in archive.namelist() if name.startswith("pdf/"))
            embedded = archive.read(pdf_name)
            page_id = _page_ids(archive.read("note"))[0]
            after = [
                value for number, wire, value
                in iter_fields(base64.b64decode(archive.read(f"page/{page_id}")))
                if number == 4 and wire == 2
            ]
        # 사용자의 PDF가 아니라 원본 캔버스에 다시 앉힌 PDF가 들어간다.
        self.assertNotEqual(embedded, variant.read_bytes())
        self._assert_canvas_preserved(embedded, origin_pdf, 2)
        # 배경만 옮기고 필기 객체는 손대지 않는다.
        self.assertEqual(after, before)
        # 쪽 크기만 같아서는 의미가 없다. 본문이 원래 있던 자리로 돌아와야 필기와 맞는다.
        with pymupdf.open(stream=embedded, filetype="pdf") as background, \
                pymupdf.open(origin_pdf) as origin:
            for index in range(background.page_count):
                old, new = ink_box(origin[index]), ink_box(background[index])
                self.assertIsNotNone(new, f"{index + 1}쪽 본문을 찾지 못했습니다.")
                self.assertAlmostEqual(new.x0, old.x0, delta=4)
                self.assertAlmostEqual(new.y0, old.y0, delta=4)

    def test_transfer_aligns_while_also_inserting_a_new_page(self):
        source, origin_pdf = self._multipage_source()
        variant = self._relaid_out(
            origin_pdf, self.root / "variant-with-cover.pdf", extra_cover=True
        )

        inspection = inspect_notewise_transfer(source, variant)
        self.assertEqual(inspection.mode, "rebuild")
        self.assertIsNotNone(inspection.alignment)

        output = self.root / "rebuilt-aligned.notewise"
        result = transfer_notewise_handwriting(source, variant, output)
        self.assertEqual(result["new_page_count"], 1)

        with ZipFile(output) as archive:
            pdf_name = next(name for name in archive.namelist() if name.startswith("pdf/"))
            embedded = archive.read(pdf_name)
            page_ids = _page_ids(archive.read("note"))
        self.assertEqual(len(page_ids), 3)
        # 짝이 없는 새 쪽도 원본 캔버스 크기를 빌려 쓴다.
        self._assert_canvas_preserved(embedded, origin_pdf, 3)

    def test_unchanged_geometry_still_embeds_the_users_pdf_byte_for_byte(self):
        output = self.root / "exact.notewise"
        transfer_notewise_handwriting(self.source, self.target_pdf, output)
        with ZipFile(output) as archive:
            pdf_name = next(name for name in archive.namelist() if name.startswith("pdf/"))
            self.assertEqual(archive.read(pdf_name), self.target_pdf.read_bytes())

    def test_rebuild_rejects_reordered_mapping(self):
        with self.assertRaisesRegex(NotewiseTransferError, "순서가 유지"):
            _validate_mapping([1, None, 0], 2)


if __name__ == "__main__":
    unittest.main()
