from __future__ import annotations

import hashlib
import struct
import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pymupdf

from pdf_page_composer.sdocx_transfer import (
    SdocxTransferError,
    inspect_transfer,
    parse_media_info,
    preview_transfer,
    transfer_handwriting,
)


def make_pdf(path: Path, labels: list[str], *, width: float = 720, height: float = 540) -> None:
    document = pymupdf.open()
    for label in labels:
        page = document.new_page(width=width, height=height)
        page.insert_text((50, 60), label, fontsize=24)
    document.save(path)
    document.close()


def make_media_info(filename: str, content: bytes) -> bytes:
    encoded_name = filename.encode("utf-16le")
    body = (
        struct.pack("<I", 0)
        + struct.pack("<H", len(filename))
        + encoded_name
        + hashlib.sha256(content).hexdigest().encode("ascii")
        + struct.pack("<H", 1)
        + struct.pack("<Q", 0)
        + b"\x01"
    )
    return struct.pack("<IH", 5500, 1) + struct.pack("<I", len(body)) + body + b"EOFX"


SPEN_FOOTER = b"\x92\x00\xa0\x0f" + bytes(122) + b"Document for S-Pen SDK"


def make_sdocx(path: Path, embedded_pdf: Path) -> dict[str, bytes]:
    """Samsung Notes 파일처럼 PDF·SPI는 무압축으로 넣고 EOCD 뒤에 꼬리표를 붙인다."""
    pdf_bytes = embedded_pdf.read_bytes()
    payloads = {
        "note.note": b"note-metadata",
        "pageIdInfo.dat": b"page-order",
        "11111111-1111-1111-1111-111111111111.page": b"P" * 420,
        "22222222-2222-2222-2222-222222222222.page": b"P" * 358,
        "media/1@page_0001.spi": b"stroke-cache",
        "media/0@source.pdf": pdf_bytes,
        "media/mediaInfo.dat": make_media_info("0@source.pdf", pdf_bytes),
        "end_tag.bin": SPEN_FOOTER,
    }
    stored = {"media/0@source.pdf", "media/1@page_0001.spi"}
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in payloads.items():
            archive.writestr(name, content, compress_type=ZIP_STORED if name in stored else None)
    with path.open("ab") as handle:
        handle.write(SPEN_FOOTER)
    return payloads


def read_footer(path: Path) -> bytes:
    data = path.read_bytes()
    position = data.rfind(b"PK\x05\x06")
    comment_length = int.from_bytes(data[position + 20:position + 22], "little")
    return data[position + 22 + comment_length:]


def raw_entries(path: Path) -> dict[str, tuple]:
    """엔트리별 헤더 정보와 압축된 바이트를 그대로 읽는다."""
    with ZipFile(path) as archive:
        handle = archive.fp
        entries = {}
        for info in archive.infolist():
            handle.seek(info.header_offset)
            header = handle.read(30)
            name_length, extra_length = struct.unpack_from("<HH", header, 26)
            handle.seek(info.header_offset + 30 + name_length + extra_length)
            entries[info.filename] = (
                info.compress_type,
                info.flag_bits,
                info.date_time,
                info.CRC,
                handle.read(info.compress_size),
            )
        return entries


class SdocxTransferTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.original_pdf = self.root / "source.pdf"
        self.target_pdf = self.root / "target.pdf"
        self.source_sdocx = self.root / "annotated.sdocx"
        make_pdf(self.original_pdf, ["SOURCE ONE", "SOURCE TWO"])
        make_pdf(self.target_pdf, ["TARGET ONE", "TARGET TWO"])
        self.original_payloads = make_sdocx(self.source_sdocx, self.original_pdf)

    def tearDown(self):
        self.folder.cleanup()

    def test_inspection_requires_matching_page_geometry(self):
        inspection = inspect_transfer(self.source_sdocx, self.target_pdf)
        self.assertEqual(inspection.page_count, 2)
        self.assertEqual(inspection.annotated_page_count, 1)
        self.assertEqual(inspection.stroke_cache_count, 1)

        mismatch = self.root / "mismatch.pdf"
        make_pdf(mismatch, ["ONLY ONE"])
        with self.assertRaisesRegex(SdocxTransferError, "페이지 수가 다릅니다"):
            inspect_transfer(self.source_sdocx, mismatch)

    def test_transfer_replaces_only_pdf_and_its_hash(self):
        output = self.root / "result.sdocx"
        source_hash_before = hashlib.sha256(self.source_sdocx.read_bytes()).hexdigest()
        result = transfer_handwriting(self.source_sdocx, self.target_pdf, output)

        self.assertEqual(result["page_count"], 2)
        self.assertTrue(output.exists())
        self.assertEqual(hashlib.sha256(self.source_sdocx.read_bytes()).hexdigest(), source_hash_before)
        with ZipFile(output) as archive:
            self.assertEqual(archive.read("media/0@source.pdf"), self.target_pdf.read_bytes())
            entries = parse_media_info(archive.read("media/mediaInfo.dat"))
            self.assertEqual(entries[0].file_hash, hashlib.sha256(self.target_pdf.read_bytes()).hexdigest())
            for name, content in self.original_payloads.items():
                if name not in {"media/0@source.pdf", "media/mediaInfo.dat"}:
                    self.assertEqual(archive.read(name), content)

    def test_transfer_aligns_a_relaid_out_pdf_to_the_original_page_box(self):
        variant = self.root / "variant.pdf"
        with pymupdf.open(self.original_pdf) as origin, pymupdf.open() as document:
            for index in range(origin.page_count):
                rect = origin[index].rect
                page = document.new_page(width=rect.width * 1.2, height=rect.height * 1.2)
                page.show_pdf_page(
                    pymupdf.Rect(60, 40, 60 + rect.width * 0.9, 40 + rect.height * 0.9),
                    origin, index,
                )
            document.save(variant)

        inspection = inspect_transfer(self.source_sdocx, variant)
        self.assertEqual(inspection.mode, "aligned")
        self.assertAlmostEqual(inspection.alignment.scale, 1 / 0.9, delta=0.02)

        output = self.root / "aligned.sdocx"
        result = transfer_handwriting(self.source_sdocx, variant, output)
        self.assertEqual(result["mode"], "aligned")

        with ZipFile(output) as archive:
            embedded = archive.read("media/0@source.pdf")
            entries = parse_media_info(archive.read("media/mediaInfo.dat"))
        # 사용자의 PDF가 아니라 원본 좌표계로 다시 앉힌 PDF가 들어가고, 해시도 그것을 가리킨다.
        self.assertNotEqual(embedded, variant.read_bytes())
        self.assertEqual(entries[0].file_hash, hashlib.sha256(embedded).hexdigest())
        self.assertEqual(read_footer(output), SPEN_FOOTER)

        with pymupdf.open(stream=embedded, filetype="pdf") as aligned, \
                pymupdf.open(self.original_pdf) as origin:
            self.assertEqual(aligned.page_count, origin.page_count)
            for index in range(origin.page_count):
                self.assertAlmostEqual(aligned[index].rect.width, origin[index].rect.width, delta=0.5)
                self.assertAlmostEqual(aligned[index].rect.height, origin[index].rect.height, delta=0.5)

    def test_preview_renders_both_backgrounds_at_the_same_size(self):
        before, after = preview_transfer(self.source_sdocx, self.target_pdf, 0)
        self.assertTrue(before.startswith(b"\x89PNG"))
        self.assertTrue(after.startswith(b"\x89PNG"))
        with pymupdf.open(stream=before, filetype="png") as left, \
                pymupdf.open(stream=after, filetype="png") as right:
            self.assertEqual(left[0].rect.width, right[0].rect.width)
            self.assertEqual(left[0].rect.height, right[0].rect.height)
        with self.assertRaises(SdocxTransferError):
            preview_transfer(self.source_sdocx, self.target_pdf, 99)

    def test_transfer_keeps_samsung_footer_and_untouched_bytes(self):
        output = self.root / "result.sdocx"
        result = transfer_handwriting(self.source_sdocx, self.target_pdf, output)

        self.assertEqual(result["footer_size"], len(SPEN_FOOTER))
        self.assertEqual(read_footer(output), SPEN_FOOTER)

        before = raw_entries(self.source_sdocx)
        after = raw_entries(output)
        self.assertEqual(list(before), list(after))
        for name in before:
            if name in {"media/0@source.pdf", "media/mediaInfo.dat"}:
                continue
            self.assertEqual(after[name], before[name], name)
        self.assertEqual(after["media/0@source.pdf"][0], ZIP_STORED)
        self.assertEqual(after["media/0@source.pdf"][4], self.target_pdf.read_bytes())
        self.assertEqual(after["media/mediaInfo.dat"][1], before["media/mediaInfo.dat"][1])

    def test_transfer_rejects_encrypted_archive(self):
        broken = self.root / "encrypted.sdocx"
        data = bytearray(self.source_sdocx.read_bytes())
        position = data.rfind(b"PK\x01\x02")
        struct.pack_into("<H", data, position + 8, 0x0001)
        broken.write_bytes(bytes(data))
        with self.assertRaises(SdocxTransferError):
            transfer_handwriting(broken, self.target_pdf, self.root / "nope.sdocx")

    def test_source_and_output_must_be_different(self):
        with self.assertRaisesRegex(SdocxTransferError, "원본 파일을 덮어쓸 수 없습니다"):
            transfer_handwriting(self.source_sdocx, self.target_pdf, self.source_sdocx)


if __name__ == "__main__":
    unittest.main()

