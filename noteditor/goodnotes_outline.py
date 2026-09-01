"""Append Goodnotes 6 outline creation events to a rebuilt event stream."""

from __future__ import annotations

import json
import secrets
import string
import struct
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .goodnotes_proto import (
    GoodnotesTransferError,
    encode_field,
    field_values,
    iter_fields,
    split_delimited,
)


@dataclass(frozen=True)
class OutlineEntry:
    page: int
    title: str


@dataclass(frozen=True)
class EventContext:
    document_id: str
    actor_id: int
    next_counter: int


PAGE_BASIS_TARGET = "target_pdf"
PAGE_BASIS_SOURCE = "source_goodnotes"
PAGE_BASES = (PAGE_BASIS_TARGET, PAGE_BASIS_SOURCE)


def validate_outline(data: Any, page_count: int) -> tuple[OutlineEntry, ...]:
    """Validate the injector-compatible ``[{page, title}, ...]`` value."""
    if not isinstance(data, list):
        raise GoodnotesTransferError("Outline JSON root must be an array.")
    if not data:
        raise GoodnotesTransferError("Outline JSON must contain at least one entry.")

    entries: list[OutlineEntry] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict) or set(item) != {"page", "title"}:
            raise GoodnotesTransferError(
                f"Outline entry {index} must contain exactly page and title."
            )
        page, title = item["page"], item["title"]
        if isinstance(page, bool) or not isinstance(page, int):
            raise GoodnotesTransferError(f"Outline entry {index}: page must be an integer.")
        if not 1 <= page <= page_count:
            raise GoodnotesTransferError(
                f"Outline entry {index}: page {page} is outside 1..{page_count}."
            )
        if not isinstance(title, str) or not title:
            raise GoodnotesTransferError(
                f"Outline entry {index}: title must be a non-empty string."
            )
        entries.append(OutlineEntry(page, title))
    return tuple(entries)


def load_outline(path: str | Path, page_count: int) -> tuple[OutlineEntry, ...]:
    """Read and validate an outline JSON file."""
    outline_path = Path(path)
    try:
        data = json.loads(outline_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GoodnotesTransferError(f"Outline JSON does not exist: {outline_path}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoodnotesTransferError(f"Outline JSON is not valid UTF-8 JSON: {exc}") from exc

    return validate_outline(data, page_count)


def map_outline_to_result(
    entries: tuple[OutlineEntry, ...] | list[OutlineEntry],
    slots: tuple[Any, ...] | list[Any],
    basis: str,
) -> tuple[OutlineEntry, ...]:
    """Translate source/target page numbers to final result-slot numbers."""
    if basis not in PAGE_BASES:
        raise GoodnotesTransferError(f"Unsupported outline page-number basis: {basis}")
    attribute = "target_index" if basis == PAGE_BASIS_TARGET else "source_index"
    mapped: list[OutlineEntry] = []
    for entry_index, entry in enumerate(entries, start=1):
        wanted = entry.page - 1
        matches = [
            result_index
            for result_index, slot in enumerate(slots)
            if getattr(slot, attribute, None) == wanted
        ]
        if len(matches) != 1:
            label = "new PDF" if basis == PAGE_BASIS_TARGET else "source Goodnotes"
            raise GoodnotesTransferError(
                f"Outline entry {entry_index}: {label} page {entry.page} does not map "
                "to exactly one result page."
            )
        mapped.append(OutlineEntry(matches[0] + 1, entry.title))
    return tuple(mapped)


def extract_event_context(events: bytes) -> EventContext:
    """Recover document, actor, and logical-counter metadata from rebuilt events."""
    document_id: str | None = None
    actor_id: int | None = None
    counters: list[int] = []
    try:
        for event in split_delimited(events):
            for event_type, wire_type, value in iter_fields(event):
                if event_type == 1 or wire_type != 2:
                    continue
                payload = bytes(value)
                fields = field_values(payload)
                if event_type == 54:
                    document = fields.get(1)
                    actor = fields.get(13)
                    if document_id is None and document and isinstance(document[0], bytes):
                        document_id = bytes(document[0]).decode("ascii")
                    if actor_id is None and actor and isinstance(actor[0], int):
                        actor_id = int(actor[0])
                counters.extend(
                    int(field_value)
                    for _number, field_wire, field_value in iter_fields(payload)
                    if field_wire == 0
                    and isinstance(field_value, int)
                    and 100_000_000_000 <= field_value < 10_000_000_000_000_000
                )
    except (UnicodeDecodeError, ValueError) as exc:
        raise GoodnotesTransferError(
            "Cannot derive outline metadata from index.events.pb."
        ) from exc
    if document_id is None or actor_id is None or not counters:
        raise GoodnotesTransferError(
            "index.events.pb has no usable page creation metadata for outlines."
        )
    return EventContext(document_id, actor_id, max(counters) + 1)


def _bytes_field(number: int, value: bytes | str) -> bytes:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return encode_field(number, 2, payload)


def _varint_field(number: int, value: int) -> bytes:
    return encode_field(number, 0, value)


def _crdt_metadata() -> bytes:
    return _bytes_field(2, _varint_field(2, secrets.randbits(32)))


def _token_prefix() -> str:
    alphabet = string.ascii_letters + string.digits + "~"
    return "".join(secrets.choice(alphabet) for _ in range(5))


def create_outline_record(
    page_id: str,
    title: str,
    context: EventContext,
    counter: int,
    position_token: str,
) -> bytes:
    """Create one length-prefixed field-65 outline creation event."""
    object_id = str(uuid.uuid4()).upper()
    event_id = str(uuid.uuid4()).upper()
    payload = b"".join(
        (
            _bytes_field(1, page_id),
            _bytes_field(2, object_id),
            _bytes_field(3, _crdt_metadata()),
            _bytes_field(4, _bytes_field(1, position_token) + _crdt_metadata()),
            _bytes_field(5, _bytes_field(1, title) + _crdt_metadata()),
            _bytes_field(6, _crdt_metadata()),
            encode_field(10, 1, struct.pack("<d", time.time() * 1000.0)),
            _bytes_field(11, event_id),
            _varint_field(13, context.actor_id),
            _varint_field(14, counter),
            _bytes_field(15, _bytes_field(1, b"") + _crdt_metadata()),
            _varint_field(16, 26),
            _bytes_field(17, context.document_id),
        )
    )
    event = _bytes_field(1, page_id) + _bytes_field(65, payload)
    return _delimited(event)


def _delimited(message: bytes) -> bytes:
    """Prefix a protobuf message with its unsigned-varint byte length."""
    from .goodnotes_proto import encode_varint

    return encode_varint(len(message)) + message


def append_outline_events(
    events: bytes,
    page_ids: list[str] | tuple[str, ...],
    entries: tuple[OutlineEntry, ...] | list[OutlineEntry],
) -> bytes:
    """Append outline records using final-result, one-based page numbers."""
    if not entries:
        return events
    context = extract_event_context(events)
    prefix = _token_prefix()
    additions = b"".join(
        create_outline_record(
            page_ids[entry.page - 1],
            entry.title,
            context,
            context.next_counter + offset,
            f"{prefix}{offset:08X}",
        )
        for offset, entry in enumerate(entries)
    )
    return events + additions


def verify_outline_events(
    events: bytes,
    page_ids: list[str] | tuple[str, ...],
    entries: tuple[OutlineEntry, ...] | list[OutlineEntry],
) -> None:
    """Ensure the requested outline records survived serialization in order."""
    if not entries:
        return
    found: list[tuple[str, str]] = []
    try:
        for record in split_delimited(events):
            envelope = field_values(record)
            for raw_payload in envelope.get(65, ()):
                if not isinstance(raw_payload, bytes):
                    continue
                payload = field_values(raw_payload)
                raw_page = payload.get(1, [None])[0]
                raw_title = payload.get(5, [None])[0]
                if not isinstance(raw_page, bytes) or not isinstance(raw_title, bytes):
                    continue
                title_fields = field_values(raw_title)
                title = title_fields.get(1, [None])[0]
                if isinstance(title, bytes):
                    found.append((raw_page.decode("ascii"), title.decode("utf-8")))
    except (UnicodeDecodeError, ValueError) as exc:
        raise GoodnotesTransferError("Cannot verify the saved Goodnotes outline.") from exc

    expected = [(page_ids[entry.page - 1], entry.title) for entry in entries]
    if found != expected:
        raise GoodnotesTransferError(
            "The saved Goodnotes outline is incomplete or out of order."
        )


__all__ = [
    "EventContext",
    "OutlineEntry",
    "PAGE_BASES",
    "PAGE_BASIS_SOURCE",
    "PAGE_BASIS_TARGET",
    "append_outline_events",
    "create_outline_record",
    "extract_event_context",
    "load_outline",
    "map_outline_to_result",
    "validate_outline",
    "verify_outline_events",
]
