"""``.goodnotes`` 아카이브의 쪽 구조를 읽고, 배경을 갈아 끼운 새 아카이브를 쓴다.

Goodnotes 6 문서의 **진짜 목차는 ``index.events.pb``** 다. 앱은 가져오기 할 때 이 사건
기록을 되짚어 문서를 만든다. ``index.notes.pb`` 는 저장 위치 색인일 뿐이라, 여기만 고치면
쪽이 하나도 없는 문서가 열린다. 그래서 이 모듈은 사건 기록을 다음 세 가지로 읽는다.

* **용지 기록**(최상위 필드 2) — 배경 첨부 파일과 그 안의 몇 쪽인지, 그리고 쪽 캔버스 크기
* **쪽 생성**(필드 54) — 쪽 개체 ID와 위 용지 참조, 정렬 키
* **쪽 연결**(필드 105) — 쪽 번호와 쪽 내용 ID

쪽 개체 ID와 내용 ID는 **붙어서 발급된다**(``개체 = 내용 − 1``). 필기가 들어 있는
``notes/<내용 ID>`` 를 쪽에 이어 붙이는 유일한 실마리라서, 새로 쓸 때도 이 규칙을 지킨다.
아무 UUID나 쓰면 앱은 문서를 열되 그 쪽을 빈 쪽으로 보여준다.

쓸 때의 원칙은 다른 형식과 같다. **필기는 건드리지 않는다** — ``notes/`` 항목은 바이트
그대로 옮기고, 배경 첨부와 그것을 가리키는 참조만 다시 쓴다. 뜻을 모르는 필드는 원본
레코드를 본떠 그대로 남긴다.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
from zipfile import ZipFile, ZipInfo

from .goodnotes_proto import (
    GoodnotesTransferError,
    encode_field,
    encode_float32,
    field_values,
    float32_field,
    int_field,
    iter_fields,
    join_delimited,
    replace_field,
    split_delimited,
    text_field,
)

# 사건 기록의 최상위 필드 번호. 이름은 공개되어 있지 않아 관측한 역할로 부른다.
_EVENT_DOCUMENT = 30
_EVENT_ATTACHMENT = 6
_EVENT_PAPER = 2
_EVENT_PAGE_CREATED = 54
_EVENT_PAGE_LINK = 105

_MEMBER_SCHEMA = "schema.pb"
_MEMBER_EVENTS = "index.events.pb"
_MEMBER_NOTES = "index.notes.pb"
_MEMBER_ATTACHMENTS = "index.attachments.pb"
_MEMBER_SEARCH = "index.search.pb"
_MEMBER_INFO = "document.info.pb"
_MEMBER_THUMBNAIL = "thumbnail.jpg"


@dataclass(frozen=True)
class GoodnotesPage:
    """출력 한 쪽을 다시 쓰는 데 필요한 원본 쪽의 모든 것."""

    order: int
    entity_id: str
    content_id: str
    paper_id: str
    attachment_id: str | None
    source_page: int
    canvas: tuple[float, float]
    notes_member: str | None
    paper_record: bytes
    page_record: bytes
    link_record: bytes | None


@dataclass(frozen=True)
class GoodnotesDocument:
    title: str
    schema_version: int
    document_id: str
    pages: tuple[GoodnotesPage, ...]
    attachments: dict[str, str]
    document_record: bytes
    attachment_record: bytes
    link_template: bytes


def _uuid_text(value: uuid.UUID) -> str:
    return str(value).upper()


def new_page_ids() -> tuple[str, str]:
    """(쪽 개체 ID, 쪽 내용 ID)를 붙은 값으로 새로 발급한다."""
    while True:
        content = uuid.uuid4()
        if content.int > 0:
            break
    return _uuid_text(uuid.UUID(int=content.int - 1)), _uuid_text(content)


def entity_of(content_id: str) -> str | None:
    try:
        content = uuid.UUID(content_id)
    except ValueError:
        return None
    if content.int == 0:
        return None
    return _uuid_text(uuid.UUID(int=content.int - 1))


def safe_members(archive: ZipFile) -> dict[str, ZipInfo]:
    members: dict[str, ZipInfo] = {}
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or "\x00" in info.filename:
            raise GoodnotesTransferError("안전하지 않은 경로가 들어 있는 Goodnotes 파일입니다.")
        if info.flag_bits & 0x1:
            raise GoodnotesTransferError("암호화된 Goodnotes 파일은 지원하지 않습니다.")
        if info.filename in members:
            raise GoodnotesTransferError(f"중복 ZIP 항목이 있습니다: {info.filename}")
        members[info.filename] = info
    return members


def _index_pairs(payload: bytes) -> list[tuple[str, str]]:
    """``{1: ID, 2: 보관 경로}`` 레코드만 모은 색인을 읽는다."""
    pairs: list[tuple[str, str]] = []
    for record in split_delimited(payload):
        fields = field_values(record)
        identifier = text_field(fields, 1)
        member = text_field(fields, 2)
        if identifier and member:
            pairs.append((identifier, member))
    return pairs


def _schema_version(payload: bytes) -> int:
    version = int_field(field_values(payload), 1)
    if version is None:
        raise GoodnotesTransferError("Goodnotes 스키마 판을 읽을 수 없습니다.")
    return version


def _canvas(payload: bytes) -> tuple[float, float] | None:
    fields = field_values(payload)
    size = fields.get(8)
    if not size or not isinstance(size[0], bytes):
        return None
    values = field_values(bytes(size[0]))
    width = float32_field(values, 1)
    height = float32_field(values, 2)
    if width and height and width > 0 and height > 0:
        return width, height
    return None


def _order_key(payload: bytes) -> str:
    fields = field_values(payload)
    wrapper = fields.get(4)
    if wrapper and isinstance(wrapper[0], bytes):
        key = text_field(field_values(bytes(wrapper[0])), 1)
        if key:
            return key
    return ""


def read_document(archive: ZipFile, members: dict[str, ZipInfo]) -> GoodnotesDocument:
    """아카이브에서 쪽 목록과 다시 쓸 때 본뜰 레코드를 읽는다."""
    for required in (_MEMBER_EVENTS, _MEMBER_SCHEMA):
        if required not in members:
            raise GoodnotesTransferError(
                f"{required} 이 없는 Goodnotes 파일은 지원하지 않습니다."
            )
    schema_version = _schema_version(archive.read(_MEMBER_SCHEMA))
    attachments = dict(_index_pairs(archive.read(_MEMBER_ATTACHMENTS))) if _MEMBER_ATTACHMENTS in members else {}
    notes = dict(_index_pairs(archive.read(_MEMBER_NOTES))) if _MEMBER_NOTES in members else {}

    document_record: bytes | None = None
    attachment_record: bytes | None = None
    link_template: bytes | None = None
    document_id = ""
    title = ""
    papers: dict[str, tuple[bytes, str | None, int, tuple[float, float] | None]] = {}
    created: list[tuple[str, str, str, bytes]] = []
    links: dict[str, bytes] = {}

    for record in split_delimited(archive.read(_MEMBER_EVENTS)):
        fields = field_values(record)
        if _EVENT_DOCUMENT in fields and document_record is None:
            document_record = record
            payload = field_values(bytes(fields[_EVENT_DOCUMENT][0]))
            document_id = text_field(payload, 1) or ""
            naming = payload.get(2)
            if naming and isinstance(naming[0], bytes):
                title = text_field(field_values(bytes(naming[0])), 1) or ""
        if _EVENT_ATTACHMENT in fields and attachment_record is None:
            attachment_record = record
        if _EVENT_PAPER in fields and isinstance(fields[_EVENT_PAPER][0], bytes):
            payload_bytes = bytes(fields[_EVENT_PAPER][0])
            payload = field_values(payload_bytes)
            paper_id = text_field(payload, 2)
            if paper_id:
                papers[paper_id] = (
                    record,
                    text_field(payload, 4),
                    int_field(payload, 5) or 1,
                    _canvas(payload_bytes),
                )
        if _EVENT_PAGE_CREATED in fields and isinstance(fields[_EVENT_PAGE_CREATED][0], bytes):
            payload_bytes = bytes(fields[_EVENT_PAGE_CREATED][0])
            payload = field_values(payload_bytes)
            entity_id = text_field(payload, 2)
            reference = payload.get(3)
            paper_id = None
            if reference and isinstance(reference[0], bytes):
                paper_id = text_field(field_values(bytes(reference[0])), 1)
            if entity_id and paper_id:
                created.append((entity_id, paper_id, _order_key(payload_bytes), record))
        if _EVENT_PAGE_LINK in fields and isinstance(fields[_EVENT_PAGE_LINK][0], bytes):
            payload = field_values(bytes(fields[_EVENT_PAGE_LINK][0]))
            content_id = text_field(payload, 4)
            # 105는 첨부 연결에도 쓰인다. 쪽 번호와 내용 ID가 다 있는 것만 쪽 연결로 본다.
            if content_id and int_field(payload, 1) is not None:
                links.setdefault(content_id, record)
                if link_template is None:
                    link_template = record

    if document_record is None or attachment_record is None:
        raise GoodnotesTransferError(
            "문서 생성·첨부 기록이 없는 Goodnotes 파일은 지원하지 않습니다."
        )
    if not created:
        raise GoodnotesTransferError("쪽 기록이 없는 Goodnotes 파일입니다.")

    # 사건 기록에 정렬 키가 있으면 그대로, 없으면 기록된 순서를 쓴다.
    if all(key for _entity, _paper, key, _record in created):
        created.sort(key=lambda item: item[2])

    pages: list[GoodnotesPage] = []
    for order, (entity_id, paper_id, _key, page_record) in enumerate(created):
        paper = papers.get(paper_id)
        if paper is None:
            raise GoodnotesTransferError(
                f"{order + 1}쪽이 가리키는 용지 기록을 찾을 수 없습니다."
            )
        paper_record, attachment_id, source_page, canvas = paper
        content_id = _uuid_text(uuid.UUID(int=uuid.UUID(entity_id).int + 1))
        notes_member = notes.get(content_id)
        if notes_member is not None and notes_member not in members:
            notes_member = None
        pages.append(
            GoodnotesPage(
                order=order,
                entity_id=entity_id,
                content_id=content_id,
                paper_id=paper_id,
                attachment_id=attachment_id,
                source_page=max(1, source_page),
                canvas=canvas or (612.0, 792.0),
                notes_member=notes_member,
                paper_record=paper_record,
                page_record=page_record,
                link_record=links.get(content_id),
            )
        )

    if link_template is None:
        raise GoodnotesTransferError(
            "쪽 연결 기록이 없는 Goodnotes 파일은 지원하지 않습니다."
        )

    return GoodnotesDocument(
        title=title,
        schema_version=schema_version,
        document_id=document_id,
        pages=tuple(pages),
        attachments=attachments,
        document_record=document_record,
        attachment_record=attachment_record,
        link_template=link_template,
    )


def background_pdf(archive: ZipFile, document: GoodnotesDocument) -> bytes:
    """쪽 순서대로 배경을 모아 하나의 PDF로 만든다. 쪽 맞추기의 원본이 된다."""
    import pymupdf

    opened: dict[str, object] = {}
    try:
        with pymupdf.open() as built:
            reference: tuple[float, float] | None = None
            for page in document.pages:
                member = document.attachments.get(page.attachment_id or "")
                source = None
                if member:
                    if member not in archive.namelist():
                        raise GoodnotesTransferError(
                            f"첨부 색인이 없는 항목을 가리킵니다: {member}"
                        )
                    if member not in opened:
                        payload = archive.read(member)
                        if not payload.startswith(b"%PDF-"):
                            raise GoodnotesTransferError(
                                "PDF가 아닌 배경을 쓰는 Goodnotes 쪽은 아직 지원하지 않습니다."
                            )
                        opened[member] = pymupdf.open(stream=payload, filetype="pdf")
                    source = opened[member]
                if source is None or not 1 <= page.source_page <= source.page_count:
                    size = reference or (page.canvas[0] / 2, page.canvas[1] / 2)
                    built.new_page(width=size[0], height=size[1])
                    continue
                index = page.source_page - 1
                if reference is None:
                    rect = source[index].rect
                    reference = (float(rect.width), float(rect.height))
                built.insert_pdf(source, from_page=index, to_page=index)
            if built.page_count < 1:
                raise GoodnotesTransferError("배경을 만들 수 있는 쪽이 없습니다.")
            return built.tobytes(garbage=4, deflate=True)
    finally:
        for handle in opened.values():
            handle.close()


def _patch_event(record: bytes, top_number: int, identity: str, patches: list) -> bytes:
    """사건 레코드의 겉 ID와 안쪽 필드만 갈아 끼우고 나머지는 그대로 둔다."""
    fields = field_values(record)
    payload = bytes(fields[top_number][0])
    for number, replacements in patches:
        payload = replace_field(payload, number, replacements)
    # 매 사건은 저마다 다른 사건 ID를 갖는다. 본떠 쓴 레코드가 ID까지 같으면 안 된다.
    payload = replace_field(payload, 11, [(2, _uuid_text(uuid.uuid4()).encode("ascii"))])
    return _rebuild_top(record, top_number, identity, payload)


def _reference_wrapper(record: bytes, top_number: int, number: int, value: str) -> bytes:
    """``{1: ID, 2: {판, 난수}}`` 꼴 참조에서 ID만 바꾼다."""
    payload = bytes(field_values(record)[top_number][0])
    wrapper = field_values(payload).get(number)
    if not wrapper or not isinstance(wrapper[0], bytes):
        return record
    inner = replace_field(bytes(wrapper[0]), 1, [(2, value.encode("ascii"))])
    return replace_field(payload, number, [(2, inner)])


def order_key(index: int) -> str:
    """사전 순으로 늘어나는 정렬 키. 자릿수를 고정해야 10쪽이 2쪽 앞으로 가지 않는다."""
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    value = index + 1
    output = ""
    for _ in range(6):
        output = digits[value % len(digits)] + output
        value //= len(digits)
    return output


def build_events(
    document: GoodnotesDocument,
    slots: list[tuple[GoodnotesPage, str, str]],
    attachment_id: str,
    attachment_size: int,
    title: str,
) -> bytes:
    """새 사건 기록을 만든다.

    문서 생성과 첨부 추가는 원본 레코드를 본뜨고, 쪽과 관련된 기록은 **결과 순서대로 새로**
    쓴다. 사라진 쪽을 가리키는 보기 상태 기록은 옮기지 않는다 — 없어도 문서는 열리고,
    남기면 없는 쪽을 가리키게 된다.

    결과에는 **새 문서 ID**를 준다. 원본과 같은 ID를 그대로 쓰면 두 파일을 다 가져왔을 때
    앱이 같은 문서로 보고 원본을 덮을 수 있다. 문서 ID는 여러 기록에 흩어져 있지만 길이가
    같은 36자 ASCII라서, 레코드 바이트에서 옛 ID를 새 ID로 바꾸면 protobuf 길이는 그대로
    두고 모든 참조를 한 번에 옮길 수 있다.
    """
    records: list[bytes] = []
    new_document_id = _uuid_text(uuid.uuid4())

    document_payload = bytes(field_values(document.document_record)[_EVENT_DOCUMENT][0])
    naming = field_values(document_payload).get(2)
    if naming and isinstance(naming[0], bytes):
        renamed = replace_field(bytes(naming[0]), 1, [(2, title.encode("utf-8"))])
        document_payload = replace_field(document_payload, 2, [(2, renamed)])
    records.append(
        _rebuild_top(document.document_record, _EVENT_DOCUMENT, document.document_id, document_payload)
    )

    records.append(
        _patch_event(
            document.attachment_record,
            _EVENT_ATTACHMENT,
            attachment_id,
            [
                (1, [(2, attachment_id.encode("ascii"))]),
                (2, [(2, attachment_id.encode("ascii"))]),
                (5, [(0, attachment_size)]),
            ],
        )
    )

    for index, (page, entity_id, content_id) in enumerate(slots):
        paper_id = _uuid_text(uuid.uuid4())
        canvas = encode_field(1, 5, encode_float32(page.canvas[0])) + encode_field(
            2, 5, encode_float32(page.canvas[1])
        )
        records.append(
            _patch_event(
                page.paper_record,
                _EVENT_PAPER,
                paper_id,
                [
                    (2, [(2, paper_id.encode("ascii"))]),
                    (4, [(2, attachment_id.encode("ascii"))]),
                    (5, [(0, index + 1)]),
                    (8, [(2, canvas)]),
                ],
            )
        )

        page_payload = _reference_wrapper(page.page_record, _EVENT_PAGE_CREATED, 3, paper_id)
        page_payload = replace_field(
            page_payload, 4, [(2, _order_wrapper(page.page_record, index))]
        )
        page_payload = replace_field(page_payload, 2, [(2, entity_id.encode("ascii"))])
        page_payload = replace_field(
            page_payload, 11, [(2, _uuid_text(uuid.uuid4()).encode("ascii"))]
        )
        records.append(
            _rebuild_top(page.page_record, _EVENT_PAGE_CREATED, entity_id, page_payload)
        )

        link_record = page.link_record or document.link_template
        records.append(
            _patch_event(
                link_record,
                _EVENT_PAGE_LINK,
                content_id,
                [
                    (1, [(0, index + 1)]),
                    (4, [(2, content_id.encode("ascii"))]),
                ],
            )
        )

    if document.document_id:
        old = document.document_id.encode("ascii")
        new = new_document_id.encode("ascii")
        if len(old) != len(new):  # 길이가 다르면 protobuf 길이 표시가 어긋난다.
            raise GoodnotesTransferError("Goodnotes 문서 ID 형식을 다룰 수 없습니다.")
        records = [record.replace(old, new) for record in records]
    return join_delimited(records)


def _order_wrapper(record: bytes, index: int) -> bytes:
    payload = bytes(field_values(record)[_EVENT_PAGE_CREATED][0])
    wrapper = field_values(payload).get(4)
    base = bytes(wrapper[0]) if wrapper and isinstance(wrapper[0], bytes) else b""
    return replace_field(base, 1, [(2, order_key(index).encode("ascii"))])


def _rebuild_top(record: bytes, top_number: int, identity: str, payload: bytes) -> bytes:
    output = bytearray()
    wrote = False
    for number, wire_type, value in iter_fields(record):
        if number == 1 and wire_type == 2:
            output.extend(encode_field(1, 2, identity.encode("ascii")))
            continue
        if number == top_number:
            output.extend(encode_field(top_number, 2, payload))
            wrote = True
            continue
        output.extend(encode_field(number, wire_type, value))
    if not wrote:
        output.extend(encode_field(top_number, 2, payload))
    return bytes(output)


def build_index(pairs: list[tuple[str, str]], extra: dict[str, list[tuple[int, int, int]]] | None = None) -> bytes:
    records = []
    for identifier, member in pairs:
        record = encode_field(1, 2, identifier.encode("ascii")) + encode_field(
            2, 2, member.encode("utf-8")
        )
        for number, wire_type, value in (extra or {}).get(identifier, []):
            record += encode_field(number, wire_type, value)
        records.append(record)
    return join_delimited(records)


__all__ = [
    "GoodnotesDocument",
    "GoodnotesPage",
    "background_pdf",
    "build_events",
    "build_index",
    "entity_of",
    "new_page_ids",
    "order_key",
    "read_document",
    "safe_members",
]
