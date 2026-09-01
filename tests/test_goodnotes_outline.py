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
from noteditor.page_plan import PlanSlot
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

    def test_creation_event_has_the_verified_field_65_shape(self) -> None:
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
