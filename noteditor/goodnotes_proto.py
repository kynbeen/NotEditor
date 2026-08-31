"""Goodnotes 6 파일의 바이트 계층: 스키마 없는 protobuf와 Apple 프레임 LZ4.

``.goodnotes`` 는 ZIP이고, 그 안의 색인·페이지는 모두 ``<varint 길이><메시지>`` 를 이어 붙인
protobuf 레코드 스트림이다. 필드 이름은 공개되어 있지 않으므로 번호로만 다룬다.

필기 획의 좌표는 다시 Apple ``libcompression`` 의 프레임 LZ4로 눌려 있다. 미리보기에서
필기를 그려 보여주려면 이걸 풀어야 하는데, 새 의존성을 하나 더 들이는 대신 여기에 직접
푼다 — 블록 형식이 단순하고, 우리는 **읽기만** 하기 때문이다. 저장할 때는 원본 획 레코드를
바이트 그대로 옮기므로 다시 누를 일이 없다.

``GoodnotesTransferError`` 도 여기 둔다. Goodnotes 계열 모듈이 모두 이 계층을 거치므로
공통 오류를 여기 놓아야 서로를 순환 참조하지 않는다.
"""
from __future__ import annotations

import struct
from typing import Iterator

from . import protobuf_wire
from .protobuf_wire import WireValue, encode_varint, join_delimited
from .transfer_plan import HandwritingTransferError


class GoodnotesTransferError(HandwritingTransferError):
    pass


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    return protobuf_wire.read_varint(data, offset, error=GoodnotesTransferError)


def iter_fields(data: bytes) -> Iterator[tuple[int, int, WireValue]]:
    return protobuf_wire.iter_fields(data, error=GoodnotesTransferError)


def field_values(data: bytes) -> dict[int, list[WireValue]]:
    return protobuf_wire.field_values(data, error=GoodnotesTransferError)


def encode_field(number: int, wire_type: int, value: WireValue) -> bytes:
    return protobuf_wire.encode_field(
        number, wire_type, value, error=GoodnotesTransferError
    )


def replace_field(
    data: bytes, number: int, replacements: list[tuple[int, WireValue]]
) -> bytes:
    return protobuf_wire.replace_field(
        data, number, replacements, error=GoodnotesTransferError
    )


def split_delimited(data: bytes) -> list[bytes]:
    return protobuf_wire.split_delimited(data, error=GoodnotesTransferError)


def text_field(fields: dict[int, list[WireValue]], number: int) -> str | None:
    """UUID·제목처럼 문자열로 읽어야 뜻이 통하는 필드만 골라 준다."""
    values = fields.get(number)
    if not values or not isinstance(values[0], bytes):
        return None
    try:
        return bytes(values[0]).decode("utf-8")
    except UnicodeDecodeError:
        return None


def int_field(fields: dict[int, list[WireValue]], number: int) -> int | None:
    values = fields.get(number)
    if not values or not isinstance(values[0], int):
        return None
    return int(values[0])


def float32_field(fields: dict[int, list[WireValue]], number: int) -> float | None:
    values = fields.get(number)
    if not values or not isinstance(values[0], bytes) or len(values[0]) != 4:
        return None
    return float(struct.unpack("<f", bytes(values[0]))[0])


def encode_float32(value: float) -> bytes:
    return struct.pack("<f", float(value))


# --- Apple libcompression 프레임 LZ4 ---------------------------------------
#
# ``bv41 <u32 푼 크기> <u32 누른 크기> <LZ4 블록>`` 이 이어지다 ``bv4$`` 로 끝난다.
# ``bv4-`` 는 누르지 않은 블록이고 뒤에 크기 하나만 온다.

_FRAME_COMPRESSED = b"bv41"
_FRAME_RAW = b"bv4-"
_FRAME_END = b"bv4$"


def _lz4_block(source: bytes, expected: int) -> bytes:
    output = bytearray()
    offset = 0
    size = len(source)
    while offset < size:
        token = source[offset]
        offset += 1
        literal_length = token >> 4
        if literal_length == 15:
            while offset < size:
                extra = source[offset]
                offset += 1
                literal_length += extra
                if extra != 255:
                    break
        end = offset + literal_length
        if end > size:
            raise GoodnotesTransferError("Goodnotes 필기 데이터가 중간에서 끝났습니다.")
        output.extend(source[offset:end])
        offset = end
        if offset + 2 > size:
            break
        (match_offset,) = struct.unpack_from("<H", source, offset)
        offset += 2
        if match_offset == 0 or match_offset > len(output):
            raise GoodnotesTransferError("Goodnotes 필기 데이터의 참조가 올바르지 않습니다.")
        match_length = (token & 0x0F) + 4
        if (token & 0x0F) == 15:
            while offset < size:
                extra = source[offset]
                offset += 1
                match_length += extra
                if extra != 255:
                    break
        start = len(output) - match_offset
        for index in range(match_length):
            output.append(output[start + index])
    if expected and len(output) != expected:
        raise GoodnotesTransferError(
            f"Goodnotes 필기 데이터 크기가 맞지 않습니다: {len(output)} ≠ {expected}"
        )
    return bytes(output)


def apple_lz4_decompress(payload: bytes) -> bytes:
    """Apple 프레임 LZ4를 푼다. 프레임이 아니면 그대로 돌려준다."""
    if not payload.startswith((_FRAME_COMPRESSED, _FRAME_RAW)):
        return payload
    output = bytearray()
    offset = 0
    while offset + 4 <= len(payload):
        magic = payload[offset:offset + 4]
        if magic == _FRAME_END:
            break
        if magic == _FRAME_COMPRESSED:
            if offset + 12 > len(payload):
                raise GoodnotesTransferError("Goodnotes 필기 블록 머리말이 잘렸습니다.")
            decompressed_size, compressed_size = struct.unpack_from("<II", payload, offset + 4)
            offset += 12
            end = offset + compressed_size
            if end > len(payload):
                raise GoodnotesTransferError("Goodnotes 필기 블록이 잘렸습니다.")
            output.extend(_lz4_block(payload[offset:end], decompressed_size))
            offset = end
        elif magic == _FRAME_RAW:
            if offset + 8 > len(payload):
                raise GoodnotesTransferError("Goodnotes 필기 블록 머리말이 잘렸습니다.")
            (raw_size,) = struct.unpack_from("<I", payload, offset + 4)
            offset += 8
            end = offset + raw_size
            if end > len(payload):
                raise GoodnotesTransferError("Goodnotes 필기 블록이 잘렸습니다.")
            output.extend(payload[offset:end])
            offset = end
        else:
            raise GoodnotesTransferError("알 수 없는 Goodnotes 필기 블록입니다.")
    return bytes(output)


__all__ = [
    "GoodnotesTransferError",
    "apple_lz4_decompress",
    "encode_field",
    "encode_float32",
    "encode_varint",
    "field_values",
    "float32_field",
    "int_field",
    "iter_fields",
    "join_delimited",
    "read_varint",
    "replace_field",
    "split_delimited",
    "text_field",
]
