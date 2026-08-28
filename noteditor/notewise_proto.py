"""Notewise 파일 안의 protobuf 메시지를 스키마 없이 읽고 다시 쓴다.

Notewise는 내보내기 형식을 공개하지 않으므로 필드 이름을 알 수 없다. 대신 protobuf 자체의
전송 규칙(필드 번호 + wire type)만으로 최상위 필드를 훑고, 손대지 않을 필드는 읽은 바이트를
그대로 되쓴다. 그래야 아직 뜻을 모르는 데이터가 저장 과정에서 사라지지 않는다.

``NotewiseTransferError``도 여기에 둔다. Notewise 계열 모듈이 모두 이 파서를 거치므로
공통 오류를 여기 놓아야 서로를 순환 참조하지 않는다.
"""
from __future__ import annotations

from typing import Iterator

from .transfer_plan import HandwritingTransferError


class NotewiseTransferError(HandwritingTransferError):
    pass


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise NotewiseTransferError("Notewise protobuf varint가 손상되었습니다.")


def iter_fields(data: bytes) -> Iterator[tuple[int, int, bytes | int]]:
    """스키마 없이 최상위 protobuf 필드를 (번호, wire type, 값)으로 흘려보낸다."""
    offset = 0
    while offset < len(data):
        key, offset = read_varint(data, offset)
        number, wire_type = key >> 3, key & 7
        if number == 0:
            raise NotewiseTransferError("Notewise protobuf 필드 번호가 올바르지 않습니다.")
        if wire_type == 0:
            value, offset = read_varint(data, offset)
        elif wire_type == 1:
            end = offset + 8
            value, offset = data[offset:end], end
        elif wire_type == 2:
            size, offset = read_varint(data, offset)
            end = offset + size
            value, offset = data[offset:end], end
        elif wire_type == 5:
            end = offset + 4
            value, offset = data[offset:end], end
        else:
            raise NotewiseTransferError(
                f"지원하지 않는 Notewise protobuf wire type입니다: {wire_type}"
            )
        if offset > len(data):
            raise NotewiseTransferError("Notewise protobuf 필드가 중간에서 끝났습니다.")
        yield number, wire_type, value


def field_values(data: bytes) -> dict[int, list[bytes | int]]:
    """같은 번호가 여러 번 나올 수 있으므로 필드 번호마다 값을 모아 준다."""
    result: dict[int, list[bytes | int]] = {}
    for number, _wire_type, value in iter_fields(data):
        result.setdefault(number, []).append(value)
    return result


def encode_varint(value: int) -> bytes:
    output = bytearray()
    while value >= 0x80:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def encode_field(number: int, wire_type: int, value: bytes | int) -> bytes:
    encoded = bytearray(encode_varint((number << 3) | wire_type))
    if wire_type == 0:
        encoded.extend(encode_varint(int(value)))
    elif wire_type == 1:
        encoded.extend(bytes(value))
    elif wire_type == 2:
        payload = bytes(value)
        encoded.extend(encode_varint(len(payload)))
        encoded.extend(payload)
    elif wire_type == 5:
        encoded.extend(bytes(value))
    else:
        raise NotewiseTransferError(
            f"지원하지 않는 Notewise protobuf wire type입니다: {wire_type}"
        )
    return bytes(encoded)
