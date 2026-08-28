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

from noteditor.notewise_ink import render_notewise_ink
from noteditor.notewise_transfer import (
    NotewiseTransferError,
    _page_ids,
    _protobuf_fields,
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


def _make_pdf(path: Path, label: str) -> None:
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=400)
        page.insert_text((30, 50), label)
        document.save(path)


def _make_notewise(path: Path, pdf: Path, annotated: bool = True) -> None:
    page_id = b"page-id"
    pdf_id = b"pdf-id"
    relation_id = b"relation-id"
    page = _field(1, page_id) + _field(3, _field(1, pdf_id))
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
    dimensions = _number(3, 300) + _number(4, 400)
    page += (
        _field(6, _field(1, dimensions))
        + _varint((7 << 3) | 1)
        + struct.pack("<d", 1024.0)
        + _field(11, relation_id)
    )
    pdf_metadata = _field(1, pdf_id) + _number(4, 1) + _field(5, pdf.name.encode())
    relation = _field(1, relation_id) + _field(2, b"source")
    note = (
        _field(1, b"note-id")
        + _field(2, b"source")
        + _field(4, page_id)
        + _field(6, pdf_metadata)
        + _field(11, relation)
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("note", base64.b64encode(note))
        archive.writestr("page/page-id", base64.b64encode(page))
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
                in _protobuf_fields(base64.b64decode(archive.read("note")))
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
                in _protobuf_fields(base64.b64decode(before))
                if number == 4 and wire == 2
            ]
            after_objects = [
                value for number, wire, value
                in _protobuf_fields(base64.b64decode(archive.read(f"page/{output_page_id}")))
                if number == 4 and wire == 2
            ]
            self.assertEqual(after_objects, before_objects)
            after_note_id = next(
                value for number, wire, value
                in _protobuf_fields(base64.b64decode(archive.read("note")))
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
            self.assertFalse(any(number == 4 for number, _wire, _value in _protobuf_fields(first_page)))
            self.assertTrue(any(number == 4 for number, _wire, _value in _protobuf_fields(second_page)))
            page_fields = [list(_protobuf_fields(page)) for page in (first_page, second_page)]
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
            background = next(value for number, wire, value in _protobuf_fields(second_page)
                              if number == 3 and wire == 2)
            page_index = next(value for number, wire, value in _protobuf_fields(background)
                              if number == 2 and wire == 0)
            self.assertEqual(page_index, 1)

    def test_rebuild_rejects_reordered_mapping(self):
        with self.assertRaisesRegex(NotewiseTransferError, "순서가 유지"):
            _validate_mapping([1, None, 0], 2)


if __name__ == "__main__":
    unittest.main()
