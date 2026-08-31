"""스키마 없이 protobuf 바이트를 읽고 되쓰는 공통 계층.

Notewise도 Goodnotes도 내보내기 형식을 공개하지 않는다. 그래서 필드 이름은 알 수 없고,
protobuf 전송 규칙(필드 번호 + wire type)만으로 훑는다. **손대지 않을 필드는 읽은 바이트를
그대로 되쓴다** — 그래야 아직 뜻을 모르는 데이터가 저장 과정에서 조용히 사라지지 않는다.

형식마다 사용자에게 보여줄 오류 이름이 다르므로 어떤 예외를 던질지는 ``error``로 받는다.
``transfer_plan`` 이 PDF 쪽에서 쓰는 방식과 같은 규약이다.
"""
from __future__ import annotations

from typing import Iterator

from .transfer_plan import HandwritingTransferError

WireValue = bytes | int


def read_varint(
    data: bytes, offset: int, *, error: type[Exception] = HandwritingTransferError
) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise error("protobuf varint가 손상되었습니다.")


def iter_fields(
    data: bytes, *, error: type[Exception] = HandwritingTransferError
) -> Iterator[tuple[int, int, WireValue]]:
    """스키마 없이 최상위 protobuf 필드를 (번호, wire type, 값)으로 흘려보낸다."""
    offset = 0
    while offset < len(data):
        key, offset = read_varint(data, offset, error=error)
        number, wire_type = key >> 3, key & 7
        if number == 0:
            raise error("protobuf 필드 번호가 올바르지 않습니다.")
        if wire_type == 0:
            value, offset = read_varint(data, offset, error=error)
        elif wire_type == 1:
            end = offset + 8
            value, offset = data[offset:end], end
        elif wire_type == 2:
            size, offset = read_varint(data, offset, error=error)
            end = offset + size
            value, offset = data[offset:end], end
        elif wire_type == 5:
            end = offset + 4
            value, offset = data[offset:end], end
        else:
            raise error(f"지원하지 않는 protobuf wire type입니다: {wire_type}")
        if offset > len(data):
            raise error("protobuf 필드가 중간에서 끝났습니다.")
        yield number, wire_type, value


def field_values(
    data: bytes, *, error: type[Exception] = HandwritingTransferError
) -> dict[int, list[WireValue]]:
    """같은 번호가 여러 번 나올 수 있으므로 필드 번호마다 값을 모아 준다."""
    result: dict[int, list[WireValue]] = {}
    for number, _wire_type, value in iter_fields(data, error=error):
        result.setdefault(number, []).append(value)
    return result


def encode_varint(value: int) -> bytes:
    output = bytearray()
    while value >= 0x80:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def encode_field(
    number: int,
    wire_type: int,
    value: WireValue,
    *,
    error: type[Exception] = HandwritingTransferError,
) -> bytes:
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
        raise error(f"지원하지 않는 protobuf wire type입니다: {wire_type}")
    return bytes(encoded)


def replace_field(
    data: bytes,
    number: int,
    replacements: list[tuple[int, WireValue]],
    *,
    error: type[Exception] = HandwritingTransferError,
) -> bytes:
    """어떤 필드 번호의 값을 통째로 갈아 끼우고 나머지는 순서까지 그대로 둔다.

    같은 번호가 여러 번 나오면 **처음 나온 자리**에 새 값들을 모아 넣는다. 없던 번호라면
    맨 뒤에 붙인다. 나머지 필드는 읽은 바이트 그대로 다시 쓴다.
    """
    output = bytearray()
    inserted = False
    for current, wire_type, value in iter_fields(data, error=error):
        if current == number:
            if not inserted:
                for replacement_wire, replacement in replacements:
                    output.extend(
                        encode_field(number, replacement_wire, replacement, error=error)
                    )
                inserted = True
            continue
        output.extend(encode_field(current, wire_type, value, error=error))
    if not inserted:
        for replacement_wire, replacement in replacements:
            output.extend(encode_field(number, replacement_wire, replacement, error=error))
    return bytes(output)


def split_delimited(
    data: bytes, *, error: type[Exception] = HandwritingTransferError
) -> list[bytes]:
    """``<varint 길이><메시지>`` 를 이어 붙인 스트림을 레코드 목록으로 나눈다."""
    records: list[bytes] = []
    offset = 0
    while offset < len(data):
        size, offset = read_varint(data, offset, error=error)
        end = offset + size
        if end > len(data):
            raise error("길이 표시 protobuf 레코드가 버퍼를 넘어갑니다.")
        records.append(data[offset:end])
        offset = end
    return records


def join_delimited(records: list[bytes]) -> bytes:
    output = bytearray()
    for record in records:
        output.extend(encode_varint(len(record)))
        output.extend(record)
    return bytes(output)


__all__ = [
    "WireValue",
    "encode_field",
    "encode_varint",
    "field_values",
    "iter_fields",
    "join_delimited",
    "read_varint",
    "replace_field",
    "split_delimited",
]
