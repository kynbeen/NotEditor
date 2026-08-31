"""Notewise 파일 안의 protobuf 메시지를 스키마 없이 읽고 다시 쓴다.

훑는 규칙 자체는 형식과 무관하므로 ``protobuf_wire`` 에 두고, 여기서는 Notewise 이름으로
오류가 나오도록 예외만 묶어 준다.

``NotewiseTransferError``도 여기에 둔다. Notewise 계열 모듈이 모두 이 파서를 거치므로
공통 오류를 여기 놓아야 서로를 순환 참조하지 않는다.
"""
from __future__ import annotations

from typing import Iterator

from . import protobuf_wire
from .protobuf_wire import WireValue, encode_varint
from .transfer_plan import HandwritingTransferError


class NotewiseTransferError(HandwritingTransferError):
    pass


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    return protobuf_wire.read_varint(data, offset, error=NotewiseTransferError)


def iter_fields(data: bytes) -> Iterator[tuple[int, int, WireValue]]:
    """스키마 없이 최상위 protobuf 필드를 (번호, wire type, 값)으로 흘려보낸다."""
    return protobuf_wire.iter_fields(data, error=NotewiseTransferError)


def field_values(data: bytes) -> dict[int, list[WireValue]]:
    """같은 번호가 여러 번 나올 수 있으므로 필드 번호마다 값을 모아 준다."""
    return protobuf_wire.field_values(data, error=NotewiseTransferError)


def encode_field(number: int, wire_type: int, value: WireValue) -> bytes:
    return protobuf_wire.encode_field(
        number, wire_type, value, error=NotewiseTransferError
    )


__all__ = [
    "NotewiseTransferError",
    "encode_field",
    "encode_varint",
    "field_values",
    "iter_fields",
    "read_varint",
]
