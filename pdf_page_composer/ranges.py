from __future__ import annotations


class PageRangeError(ValueError):
    pass


def parse_page_ranges(text: str, page_count: int) -> list[int]:
    """Parse a 1-based range expression into ordered, unique 0-based indices."""
    if page_count < 1:
        raise PageRangeError("페이지가 없는 PDF입니다.")
    value = (text or "").strip()
    if not value:
        return []

    result: list[int] = []
    seen: set[int] = set()
    for raw_part in value.replace("，", ",").split(","):
        part = raw_part.strip()
        if not part:
            raise PageRangeError("쉼표 사이에 빈 페이지 범위가 있습니다.")
        if "-" in part:
            pieces = [piece.strip() for piece in part.split("-")]
            if len(pieces) != 2 or not all(piece.isdigit() for piece in pieces):
                raise PageRangeError(f"올바르지 않은 범위입니다: {part}")
            start, end = map(int, pieces)
            if start > end:
                raise PageRangeError(f"시작 쪽이 끝 쪽보다 큽니다: {part}")
            pages = range(start, end + 1)
        else:
            if not part.isdigit():
                raise PageRangeError(f"올바르지 않은 쪽 번호입니다: {part}")
            pages = (int(part),)

        for page_number in pages:
            if not 1 <= page_number <= page_count:
                raise PageRangeError(
                    f"{page_number}쪽은 문서 범위(1-{page_count}) 밖입니다."
                )
            index = page_number - 1
            if index not in seen:
                seen.add(index)
                result.append(index)
    return result


def format_page_ranges(indices: list[int]) -> str:
    """Format sorted unique 0-based indices as compact 1-based ranges."""
    pages = sorted({int(index) + 1 for index in indices})
    if not pages:
        return ""
    chunks: list[str] = []
    start = previous = pages[0]
    for page in pages[1:]:
        if page == previous + 1:
            previous = page
            continue
        chunks.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = page
    chunks.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(chunks)
