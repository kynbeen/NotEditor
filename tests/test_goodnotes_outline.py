from __future__ import annotations

import json
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path

from noteditor.goodnotes_archive import (
    build_events,
    new_page_ids,
    read_document,
    safe_members,
)
from noteditor.goodnotes_outline import (
    EventContext,
    OutlineEntry,
    PAGE_BASIS_SOURCE,
    PAGE_BASIS_TARGET,
    append_outline_events,
    create_outline_record,
    extract_event_context,
    load_outline,
    map_outline_to_result,
    verify_outline_events,
)
from noteditor.goodnotes_proto import (
    GoodnotesTransferError,
    field_values,
    iter_fields,
    split_delimited,
)
from noteditor.page_plan import PagePlan, PlanSlot
FIXTURE = Path(__file__).parent / "fixtures" / "goodnotes" / "gn-mac-mixed-pens.goodnotes"


def _outline_payload(record: bytes) -> bytes:
    value = field_values(record)[65][0]
    assert isinstance(value, bytes)
    return value


class OutlineJsonTest(unittest.TestCase):
    def test_loads_injector_format_without_changing_titles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outline.json"
            path.write_text(
                json.dumps(
                    [
                        {"page": 3, "title": " 1. 앞 공백"},
                        {"page": 1, "title": "한국어 💜"},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                load_outline(path, 3),
                (
                    OutlineEntry(3, " 1. 앞 공백"),
                    OutlineEntry(1, "한국어 💜"),
                ),
            )

    def test_rejects_pages_outside_the_final_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outline.json"
            path.write_text('[{"page": 4, "title": "bad"}]', encoding="utf-8")
            with self.assertRaises(GoodnotesTransferError):
                load_outline(path, 3)

    def test_rejects_extra_fields_and_boolean_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outline.json"
            for payload in (
                '[{"page": 1, "title": "A", "level": 1}]',
                '[{"page": true, "title": "A"}]',
            ):
                path.write_text(payload, encoding="utf-8")
                with self.assertRaises(GoodnotesTransferError):
                    load_outline(path, 1)


class OutlineEventTest(unittest.TestCase):
    def setUp(self) -> None:
        self.context = EventContext(
            "11111111-1111-1111-1111-111111111111", 12345, 900_000_000_000
        )

    def test_counter_uses_schema_fields_not_actor_id_magnitude(self):
        from noteditor.goodnotes_proto import encode_field, encode_varint
        actor = 900_000_000_000
        payload = (encode_field(1, 2, self.context.document_id.encode())
                   + encode_field(13, 0, actor) + encode_field(14, 0, 7))
        event = encode_field(54, 2, payload)
        self.assertEqual(extract_event_context(encode_varint(len(event)) + event),
                         EventContext(self.context.document_id, actor, 8))

    def test_creation_event_has_the_proposed_field_65_shape(self) -> None:
        page_id = "22222222-2222-2222-2222-222222222222"
        framed = create_outline_record(page_id, "한국어 💜", self.context, 7, "token00000000")
        record = split_delimited(framed)[0]
        envelope = [(number, wire) for number, wire, _value in iter_fields(record)]
        self.assertEqual(envelope, [(1, 2), (65, 2)])
        payload = _outline_payload(record)
        self.assertEqual(
            [(number, wire) for number, wire, _value in iter_fields(payload)],
            [
                (1, 2), (2, 2), (3, 2), (4, 2), (5, 2), (6, 2),
                (10, 1), (11, 2), (13, 0), (14, 0), (15, 2),
                (16, 0), (17, 2),
            ],
        )
        fields = field_values(payload)
        self.assertEqual(fields[1][0], page_id.encode("ascii"))
        self.assertEqual(field_values(bytes(fields[5][0]))[1][0], "한국어 💜".encode())
        uuid.UUID(bytes(fields[2][0]).decode("ascii"))
        uuid.UUID(bytes(fields[11][0]).decode("ascii"))

    def test_append_preserves_prefix_page_mapping_order_and_counters(self) -> None:
        # A minimal page-create event supplies the same metadata that rebuilt
        # NotEditor event streams carry.
        from noteditor.goodnotes_proto import encode_field, encode_varint

        page_create = b"".join(
            (
                encode_field(1, 2, self.context.document_id.encode("ascii")),
                encode_field(13, 0, self.context.actor_id),
                encode_field(14, 0, self.context.next_counter - 1),
            )
        )
        event = encode_field(54, 2, page_create)
        original = encode_varint(len(event)) + event
        page_ids = (
            "33333333-3333-3333-3333-333333333333",
            "44444444-4444-4444-4444-444444444444",
        )
        output = append_outline_events(
            original,
            page_ids,
            (OutlineEntry(2, "B"), OutlineEntry(1, " A")),
        )
        self.assertTrue(output.startswith(original))
        additions = split_delimited(output)[1:]
        payloads = [_outline_payload(record) for record in additions]
        self.assertEqual([field_values(p)[1][0] for p in payloads], [
            page_ids[1].encode("ascii"), page_ids[0].encode("ascii")
        ])
        self.assertEqual([field_values(p)[14][0] for p in payloads], [
            self.context.next_counter, self.context.next_counter + 1
        ])
        tokens = [field_values(bytes(field_values(p)[4][0]))[1][0] for p in payloads]
        self.assertEqual(tokens, sorted(tokens))

    def test_verification_keeps_first_entries_and_same_page_entries(self) -> None:
        from noteditor.goodnotes_proto import encode_field, encode_varint

        page_create = b"".join((
            encode_field(1, 2, self.context.document_id.encode("ascii")),
            encode_field(13, 0, self.context.actor_id),
            encode_field(14, 0, self.context.next_counter - 1),
        ))
        event = encode_field(54, 2, page_create)
        original = encode_varint(len(event)) + event
        page_ids = ("33333333-3333-3333-3333-333333333333",)
        entries = (OutlineEntry(1, "first"), OutlineEntry(1, "second"), OutlineEntry(1, "third"))
        output = append_outline_events(original, page_ids, entries)

        verify_outline_events(output, page_ids, entries)
        with self.assertRaises(GoodnotesTransferError):
            verify_outline_events(output, page_ids, entries[1:])

    def test_maps_new_pdf_pages_through_the_final_page_plan(self) -> None:
        slots = (
            PlanSlot(0, 0, confirmed=True),
            PlanSlot(1, None, confirmed=True),
            PlanSlot(2, 1, confirmed=True),
        )
        self.assertEqual(
            map_outline_to_result(
                (OutlineEntry(2, "target two"),), slots, PAGE_BASIS_TARGET
            ),
            (OutlineEntry(3, "target two"),),
        )

    def test_maps_source_goodnotes_pages_through_the_final_page_plan(self) -> None:
        slots = (
            PlanSlot(0, 0, confirmed=True),
            PlanSlot(1, None, confirmed=True),
            PlanSlot(2, 1, confirmed=True),
        )
        self.assertEqual(
            map_outline_to_result(
                (OutlineEntry(2, "source two"),), slots, PAGE_BASIS_SOURCE
            ),
            (OutlineEntry(2, "source two"),),
        )


class OutlineTransferIntegrationTest(unittest.TestCase):
    def setUp(self):
        import pymupdf
        from noteditor.goodnotes_archive import background_pdf

        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.target = self.root / "tall.pdf"
        self.output = self.root / "outlined.goodnotes"
        with zipfile.ZipFile(FIXTURE) as archive:
            document = read_document(archive, safe_members(archive))
            embedded = background_pdf(archive, document)
        with pymupdf.open(stream=embedded, filetype="pdf") as source, pymupdf.open() as target:
            rect = source[0].rect
            self.ratio = rect.width / (rect.height + 120)
            for _ in range(2):
                page = target.new_page(width=rect.width, height=rect.height + 120)
                page.show_pdf_page(pymupdf.Rect(0, 60, rect.width, rect.height + 60), source, 0)
            target.save(self.target)

    def test_outline_survives_reorder_with_ratio_and_ink_validation(self):
        import pymupdf
        from noteditor.goodnotes_archive import background_pdf
        from noteditor.goodnotes_ink import count_goodnotes_strokes
        from noteditor.goodnotes_transfer import transfer_goodnotes_handwriting

        original = FIXTURE.read_bytes()
        plan = PagePlan(1, 2, (PlanSlot(None, 1, confirmed=True), PlanSlot(0, 0, confirmed=True)))
        for basis, page, result_page in ((PAGE_BASIS_TARGET, 2, 1), (PAGE_BASIS_SOURCE, 1, 2)):
            with self.subTest(basis=basis):
                result = transfer_goodnotes_handwriting(
                    FIXTURE, self.target, self.output, plan_override=plan,
                    outline_entries=[{"page": page, "title": "Chapter"}], outline_page_basis=basis,
                )
                self.assertEqual(result["outline_count"], 1)
                with zipfile.ZipFile(self.output) as archive:
                    document = read_document(archive, safe_members(archive))
                    verify_outline_events(archive.read("index.events.pb"),
                                          [p.entity_id for p in document.pages],
                                          (OutlineEntry(result_page, "Chapter"),))
                    self.assertEqual(count_goodnotes_strokes(archive.read(document.pages[0].notes_member)), 0)
                    self.assertGreater(count_goodnotes_strokes(archive.read(document.pages[1].notes_member)), 0)
                    with pymupdf.open(stream=background_pdf(archive, document), filetype="pdf") as pdf:
                        self.assertEqual(pdf.page_count, 2)
                        self.assertAlmostEqual(pdf[0].rect.width / pdf[0].rect.height, self.ratio, places=4)
                self.assertEqual(FIXTURE.read_bytes(), original)

    def test_excluded_outline_page_rejects_without_replacing_output(self):
        from noteditor.goodnotes_transfer import transfer_goodnotes_handwriting
        plan = PagePlan(1, 2, (PlanSlot(0, 0, confirmed=True),), excluded_targets=(1,))
        self.output.write_bytes(b"previous output")
        with self.assertRaises(GoodnotesTransferError):
            transfer_goodnotes_handwriting(
                FIXTURE, self.target, self.output, plan_override=plan,
                outline_entries=[{"page": 2, "title": "Excluded"}],
            )
        self.assertEqual(self.output.read_bytes(), b"previous output")
        self.assertEqual(list(self.root.glob("*.tmp.goodnotes")), [])

    def test_existing_outline_is_not_silently_discarded(self):
        from noteditor.goodnotes_transfer import transfer_goodnotes_handwriting
        plan = PagePlan(1, 2, (PlanSlot(0, 0, confirmed=True), PlanSlot(None, 1, confirmed=True)))
        transfer_goodnotes_handwriting(
            FIXTURE, self.target, self.output, plan_override=plan,
            outline_entries=[{"page": 1, "title": "Existing"}],
        )
        prior = self.output.read_bytes()
        destination = self.root / "second.goodnotes"
        with self.assertRaisesRegex(GoodnotesTransferError, "목차"):
            transfer_goodnotes_handwriting(self.output, self.target, destination)
        self.assertEqual(self.output.read_bytes(), prior)
        self.assertFalse(destination.exists())

    def test_android_dispatch_carries_outline_options(self):
        from noteditor.app import ComposerApi
        from noteditor.goodnotes_transfer import inspect_goodnotes_transfer
        from unittest.mock import patch
        api = ComposerApi()
        self.addCleanup(api._close)
        api._handwriting_source, api._handwriting_target = FIXTURE, self.target
        inspection = inspect_goodnotes_transfer(FIXTURE, self.target)
        with patch.object(api, "_inspection", return_value=inspection):
            result = json.loads(api.dispatch_call("transfer_handwriting_to_path", json.dumps([
                str(self.output), [{"source_index": 0, "target_index": 0, "confirmed": True},
                                   {"source_index": None, "target_index": 1, "confirmed": True}],
                True, [{"page": 2, "title": "Android"}], PAGE_BASIS_TARGET,
            ])))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"]["outline_count"], 1)

    def test_rebuilt_events_attach_outline_to_the_final_result_page_uuid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outline = root / "outline.json"
            outline.write_text(
                '[{"page": 2, "title": " Final page"}]', encoding="utf-8"
            )
            with zipfile.ZipFile(FIXTURE) as archive:
                document = read_document(archive, safe_members(archive))
            first = new_page_ids()
            second = new_page_ids()
            slots = [
                (document.pages[0], first[0], first[1]),
                (document.pages[0], second[0], second[1]),
            ]
            events = build_events(document, slots, str(uuid.uuid4()).upper(), 100, "Target")
            entries = load_outline(outline, len(slots))
            output = append_outline_events(events, [first[0], second[0]], entries)

            self.assertTrue(output.startswith(events))
            records = split_delimited(output)
            outlines = [record for record in records if 65 in field_values(record)]
            self.assertEqual(len(outlines), 1)
            payload = _outline_payload(outlines[0])
            self.assertEqual(field_values(payload)[1][0], second[0].encode("ascii"))
            self.assertEqual(
                field_values(bytes(field_values(payload)[5][0]))[1][0], b" Final page"
            )


if __name__ == "__main__":
    unittest.main()
