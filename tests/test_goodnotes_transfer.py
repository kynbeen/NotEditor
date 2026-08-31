"""Goodnotes 6 필기 이전 검사.

이 검사는 **실제 Goodnotes 6 내보내기 파일**(`tests/fixtures/goodnotes/`)을 기준으로 한다.
합성 파일만으로는 "우리가 만든 구조를 우리가 다시 읽었다"는 것밖에 증명하지 못하고, 이
형식에서 정작 중요한 것은 앱이 실제로 쓰는 구조를 우리가 안 깨뜨리는지이기 때문이다.
"""
from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

import pymupdf

from noteditor.goodnotes_archive import (
    background_pdf,
    entity_of,
    new_page_ids,
    order_key,
    read_document,
    safe_members,
)
from noteditor.goodnotes_ink import read_goodnotes_strokes, render_goodnotes_ink
from noteditor.goodnotes_proto import (
    GoodnotesTransferError,
    apple_lz4_decompress,
    field_values,
    split_delimited,
)
from noteditor.goodnotes_transfer import (
    inspect_goodnotes_transfer,
    preview_goodnotes_transfer,
    transfer_goodnotes_handwriting,
)
from noteditor.handwriting_transfer import (
    SUPPORTED_SUFFIXES,
    output_suffix,
    with_output_suffix,
)
from noteditor.page_plan import PagePlan, PlanSlot

FIXTURE = Path(__file__).parent / "fixtures" / "goodnotes" / "gn-mac-mixed-pens.goodnotes"

# 실제 파일에서 확인한 값. 바뀌면 형식 해석이 틀어진 것이다.
PAGE_WIDTH, PAGE_HEIGHT = 455.0400085449219, 588.4500122070312
CANVAS = (834.239990234375, 1078.824951171875)


def _make_pdf(path: Path, label: str, pages: int = 1) -> None:
    with pymupdf.open() as document:
        for index in range(pages):
            page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
            page.insert_text((50, 80), f"{label} {index + 1}", fontsize=20)
        document.save(path)


class GoodnotesArchiveTest(unittest.TestCase):
    """실제 내보내기 파일의 구조를 읽어 낸다."""

    def setUp(self) -> None:
        self.archive = zipfile.ZipFile(FIXTURE)
        self.addCleanup(self.archive.close)
        self.document = read_document(self.archive, safe_members(self.archive))

    def test_reads_document_and_single_page(self) -> None:
        self.assertEqual(self.document.schema_version, 25)
        self.assertEqual(self.document.title, "Untitled Notebook")
        self.assertEqual(len(self.document.pages), 1)

    def test_page_carries_background_reference_and_canvas(self) -> None:
        page = self.document.pages[0]
        self.assertEqual(page.source_page, 1)
        self.assertIn(page.attachment_id, self.document.attachments)
        self.assertAlmostEqual(page.canvas[0], CANVAS[0], places=3)
        self.assertAlmostEqual(page.canvas[1], CANVAS[1], places=3)
        self.assertIsNotNone(page.notes_member)

    def test_page_entity_and_content_ids_are_adjacent(self) -> None:
        """``개체 = 내용 − 1``. 이걸 어기면 앱은 그 쪽을 빈 쪽으로 연다."""
        page = self.document.pages[0]
        self.assertEqual(entity_of(page.content_id), page.entity_id)

    def test_background_pdf_matches_the_attachment_geometry(self) -> None:
        payload = background_pdf(self.archive, self.document)
        with pymupdf.open(stream=payload, filetype="pdf") as document:
            self.assertEqual(document.page_count, 1)
            self.assertAlmostEqual(document[0].rect.width, PAGE_WIDTH, places=2)
            self.assertAlmostEqual(document[0].rect.height, PAGE_HEIGHT, places=2)

    def test_rejects_archive_without_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "broken.goodnotes"
            with zipfile.ZipFile(broken, "w") as result:
                result.writestr("schema.pb", b"\x08\x19")
            with zipfile.ZipFile(broken) as archive:
                with self.assertRaises(GoodnotesTransferError):
                    read_document(archive, safe_members(archive))

    def test_order_keys_sort_in_page_order(self) -> None:
        keys = [order_key(index) for index in range(120)]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(set(keys)), len(keys))

    def test_new_page_ids_keep_the_adjacency_rule(self) -> None:
        for _ in range(50):
            entity_id, content_id = new_page_ids()
            self.assertEqual(entity_of(content_id), entity_id)


class GoodnotesInkTest(unittest.TestCase):
    """압축된 획 데이터를 풀어 미리보기로 그린다."""

    def setUp(self) -> None:
        with zipfile.ZipFile(FIXTURE) as archive:
            member = next(n for n in archive.namelist() if n.startswith("notes/"))
            self.payload = archive.read(member)

    def test_apple_lz4_frames_decompress_to_tpl_blocks(self) -> None:
        blobs = []
        for record in split_delimited(self.payload):
            fields = field_values(record)
            if 7 not in fields:
                continue
            stroke = field_values(bytes(fields[7][0]))
            geometry = stroke.get(2)
            if geometry and isinstance(geometry[0], bytes) and geometry[0]:
                blobs.append(apple_lz4_decompress(bytes(geometry[0])))
        self.assertEqual(len(blobs), 7)
        for blob in blobs:
            self.assertTrue(blob.startswith(b"tpl\0"))

    def test_reads_every_pen_family_with_documented_widths(self) -> None:
        strokes = read_goodnotes_strokes(self.payload)
        kinds = {stroke.kind for stroke in strokes}
        self.assertEqual(kinds, {"pen", "ball", "pencil", "highlighter", "marker"})
        widths = {
            stroke.kind: round(stroke.widths[0], 2)
            for stroke in strokes
            if stroke.kind in ("ball", "highlighter", "marker")
        }
        # Goodnotes의 기본 굵기. 서명 앞의 u16을 굵기로 잘못 읽으면 전부 2.0이 된다.
        self.assertEqual(widths["ball"], 1.56)
        self.assertEqual(widths["highlighter"], 24.0)
        self.assertEqual(widths["marker"], 18.0)

    def test_strokes_stay_inside_the_page_canvas(self) -> None:
        strokes = read_goodnotes_strokes(self.payload)
        self.assertTrue(strokes)
        for stroke in strokes:
            for x, y in stroke.points:
                self.assertGreaterEqual(x, 0.0)
                self.assertGreaterEqual(y, 0.0)
                self.assertLessEqual(x, CANVAS[0])
                self.assertLessEqual(y, CANVAS[1])

    def test_highlighter_is_translucent(self) -> None:
        strokes = read_goodnotes_strokes(self.payload)
        highlighter = next(s for s in strokes if s.kind == "highlighter")
        self.assertLess(highlighter.opacity, 1.0)
        self.assertGreater(highlighter.opacity, 0.0)

    def test_renders_a_transparent_layer(self) -> None:
        png, count = render_goodnotes_ink(self.payload, (200, 259), CANVAS)
        self.assertEqual(count, 6)
        self.assertTrue(png.startswith(b"\x89PNG"))


class GoodnotesTransferTest(unittest.TestCase):
    """배경을 갈아 끼워 저장하고 결과를 다시 읽는다."""

    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: None)
        self.target = self.directory / "새-배경.pdf"
        self.output = self.directory / "결과.goodnotes"

    def _source_notes(self) -> bytes:
        with zipfile.ZipFile(FIXTURE) as archive:
            member = next(n for n in archive.namelist() if n.startswith("notes/"))
            return archive.read(member)

    def test_inspection_reports_the_source_page_and_strokes(self) -> None:
        _make_pdf(self.target, "Slide", pages=1)
        inspection = inspect_goodnotes_transfer(FIXTURE, self.target)
        self.assertEqual(inspection.source_page_count, 1)
        self.assertEqual(inspection.page_count, 1)
        self.assertEqual(inspection.annotated_page_count, 1)
        self.assertEqual(inspection.stroke_cache_count, 7)
        self.assertEqual(inspection.target_name, self.target.name)

    def test_handwriting_journal_survives_byte_for_byte(self) -> None:
        """이 형식 지원의 핵심. 획을 다시 인코딩하면 앱에서 뭉개진 자국으로 열린다."""
        _make_pdf(self.target, "Lecture", pages=3)
        plan = PagePlan(
            source_count=1,
            target_count=3,
            slots=(
                PlanSlot(None, 0, confirmed=True),
                PlanSlot(0, 1, confirmed=True, manual=True),
                PlanSlot(None, 2, confirmed=True),
            ),
        )
        result = transfer_goodnotes_handwriting(
            FIXTURE, self.target, self.output, plan_override=plan
        )
        self.assertEqual(result["page_count"], 3)
        self.assertEqual(result["new_page_count"], 2)

        with zipfile.ZipFile(self.output) as archive:
            document = read_document(archive, safe_members(archive))
            self.assertEqual(len(document.pages), 3)
            blobs = [
                archive.read(page.notes_member) if page.notes_member else b""
                for page in document.pages
            ]
        self.assertEqual(blobs[0], b"")
        self.assertEqual(blobs[2], b"")
        self.assertEqual(blobs[1], self._source_notes())

    def test_output_pages_point_at_the_new_background_in_order(self) -> None:
        _make_pdf(self.target, "Slide", pages=2)
        plan = PagePlan(
            source_count=1,
            target_count=2,
            slots=(
                PlanSlot(0, 0, confirmed=True),
                PlanSlot(None, 1, confirmed=True),
            ),
        )
        transfer_goodnotes_handwriting(
            FIXTURE, self.target, self.output, plan_override=plan
        )
        with zipfile.ZipFile(self.output) as archive:
            document = read_document(archive, safe_members(archive))
            self.assertEqual(len(document.attachments), 1)
            attachment_id = next(iter(document.attachments))
            for index, page in enumerate(document.pages):
                self.assertEqual(page.attachment_id, attachment_id)
                self.assertEqual(page.source_page, index + 1)
                self.assertEqual(entity_of(page.content_id), page.entity_id)
            payload = background_pdf(archive, document)
        with pymupdf.open(stream=payload, filetype="pdf") as built:
            self.assertEqual(built.page_count, 2)
            self.assertIn("Slide 1", built[0].get_text())
            self.assertIn("Slide 2", built[1].get_text())

    def test_canvas_is_preserved_so_the_ink_stays_in_place(self) -> None:
        _make_pdf(self.target, "Slide", pages=1)
        plan = PagePlan(
            source_count=1,
            target_count=1,
            slots=(PlanSlot(0, 0, confirmed=True),),
        )
        transfer_goodnotes_handwriting(
            FIXTURE, self.target, self.output, plan_override=plan
        )
        with zipfile.ZipFile(self.output) as archive:
            document = read_document(archive, safe_members(archive))
            page = document.pages[0]
            self.assertAlmostEqual(page.canvas[0], CANVAS[0], places=3)
            self.assertAlmostEqual(page.canvas[1], CANVAS[1], places=3)

    def test_writes_the_members_the_app_writes(self) -> None:
        """앱이 쓰는 항목을 빠뜨리면 가져오기가 통째로 거절된다."""
        _make_pdf(self.target, "Slide", pages=1)
        transfer_goodnotes_handwriting(FIXTURE, self.target, self.output)
        with zipfile.ZipFile(FIXTURE) as original:
            expected = {
                name.split("/")[0] for name in original.namelist()
            }
        with zipfile.ZipFile(self.output) as archive:
            produced = {name.split("/")[0] for name in archive.namelist()}
            self.assertEqual(archive.read("schema.pb"), b"\x08\x19")
        self.assertEqual(produced, expected)

    def test_page_ids_are_new_so_the_original_is_not_shadowed(self) -> None:
        _make_pdf(self.target, "Slide", pages=1)
        transfer_goodnotes_handwriting(FIXTURE, self.target, self.output)
        with zipfile.ZipFile(FIXTURE) as original:
            source = read_document(original, safe_members(original))
        with zipfile.ZipFile(self.output) as archive:
            result = read_document(archive, safe_members(archive))
        source_ids = {page.content_id for page in source.pages}
        result_ids = {page.content_id for page in result.pages}
        self.assertFalse(source_ids & result_ids)

    def test_our_own_output_is_a_valid_source_again(self) -> None:
        """두 번 옮겨도 필기는 그대로다. 결과를 다시 읽지 못하면 배경을 두 번 못 바꾼다."""
        first_pdf = self.directory / "first.pdf"
        second_pdf = self.directory / "second.pdf"
        _make_pdf(first_pdf, "First", pages=2)
        _make_pdf(second_pdf, "Second", pages=4)

        step_one = self.directory / "1단계.goodnotes"
        transfer_goodnotes_handwriting(
            FIXTURE,
            first_pdf,
            step_one,
            plan_override=PagePlan(
                1,
                2,
                (PlanSlot(0, 0, confirmed=True), PlanSlot(None, 1, confirmed=True)),
            ),
        )
        step_two = self.directory / "2단계.goodnotes"
        transfer_goodnotes_handwriting(
            step_one,
            second_pdf,
            step_two,
            plan_override=PagePlan(
                2,
                4,
                (
                    PlanSlot(None, 0, confirmed=True),
                    PlanSlot(None, 1, confirmed=True),
                    PlanSlot(0, 2, confirmed=True, manual=True),
                    PlanSlot(1, 3, confirmed=True, manual=True),
                ),
            ),
        )
        with zipfile.ZipFile(step_two) as archive:
            document = read_document(archive, safe_members(archive))
            blobs = [
                archive.read(page.notes_member) if page.notes_member else b""
                for page in document.pages
            ]
        self.assertEqual(len(blobs), 4)
        self.assertEqual(blobs[2], self._source_notes())
        self.assertEqual([blob for blob in blobs if blob], [self._source_notes()])

    def test_result_gets_its_own_document_id(self) -> None:
        """원본과 같은 문서 ID면 둘 다 가져왔을 때 앱이 원본을 덮을 수 있다."""
        _make_pdf(self.target, "Slide", pages=1)
        transfer_goodnotes_handwriting(FIXTURE, self.target, self.output)
        with zipfile.ZipFile(FIXTURE) as original:
            source = read_document(original, safe_members(original))
        with zipfile.ZipFile(self.output) as archive:
            result = read_document(archive, safe_members(archive))
            events = archive.read("index.events.pb")
        self.assertTrue(result.document_id)
        self.assertNotEqual(result.document_id, source.document_id)
        # 옛 ID가 어느 기록에도 남아 있으면 안 된다.
        self.assertNotIn(source.document_id.encode("ascii"), events)

    def test_refuses_to_overwrite_the_source(self) -> None:
        _make_pdf(self.target, "Slide", pages=1)
        with self.assertRaises(GoodnotesTransferError):
            transfer_goodnotes_handwriting(FIXTURE, self.target, FIXTURE)

    def test_requires_a_goodnotes_source_and_pdf_target(self) -> None:
        _make_pdf(self.target, "Slide", pages=1)
        with self.assertRaises(GoodnotesTransferError):
            inspect_goodnotes_transfer(self.target, self.target)
        with self.assertRaises(GoodnotesTransferError):
            inspect_goodnotes_transfer(FIXTURE, FIXTURE)

    def test_preview_draws_the_ink_over_the_new_background(self) -> None:
        _make_pdf(self.target, "Slide", pages=1)
        before, after, ink, count = preview_goodnotes_transfer(
            FIXTURE, self.target, 0, source_index_override=0
        )
        self.assertEqual(count, 6)
        for payload in (before, after, ink):
            self.assertTrue(payload.startswith(b"\x89PNG"))

    def test_preview_of_a_new_page_has_no_ink(self) -> None:
        _make_pdf(self.target, "Slide", pages=1)
        _before, _after, _ink, count = preview_goodnotes_transfer(
            FIXTURE, self.target, 0, source_index_override=-1
        )
        self.assertEqual(count, 0)


class GoodnotesDispatchTest(unittest.TestCase):
    """UI는 형식을 몰라야 한다. 확장자 판단은 한곳에만 있다."""

    def test_goodnotes_is_an_accepted_source(self) -> None:
        self.assertIn(".goodnotes", SUPPORTED_SUFFIXES)

    def test_result_keeps_the_source_format(self) -> None:
        self.assertEqual(output_suffix("a.goodnotes"), ".goodnotes")
        self.assertEqual(output_suffix("a.GoodNotes"), ".goodnotes")

    def test_output_name_swaps_a_mixed_handwriting_extension(self) -> None:
        self.assertEqual(
            with_output_suffix("강의.notewise", "원본.goodnotes"), "강의.goodnotes"
        )
        self.assertEqual(
            with_output_suffix("강의 v1.2", "원본.goodnotes"), "강의 v1.2.goodnotes"
        )


if __name__ == "__main__":
    unittest.main()
