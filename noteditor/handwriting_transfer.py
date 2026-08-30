"""필기 문서 확장자를 보고 형식별 이전 구현으로 보낸다.

UI(데스크톱 ``app.py``, 웹 ``web.py``)는 어떤 필기 앱에서 나온 파일인지 몰라야 한다. 형식을
가르는 판단은 여기 한곳에만 두고, 위쪽에는 ``inspect_transfer``/``preview_transfer``/
``transfer_handwriting`` 세 함수와 공통 오류 ``HandwritingTransferError`` 만 보인다.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .notewise_transfer import (
    inspect_notewise_transfer,
    preview_notewise_transfer,
    transfer_notewise_handwriting,
)
from .sdocx_transfer import (
    inspect_transfer as inspect_sdocx_transfer,
    preview_transfer as preview_sdocx_transfer,
    transfer_handwriting as transfer_sdocx_handwriting,
)
from .transfer_plan import HandwritingTransferError, TransferInspection

SUPPORTED_SUFFIXES = (".sdocx", ".notewise")


def _is_notewise(source: str | Path) -> bool:
    return Path(source).suffix.lower() == ".notewise"


def inspect_transfer(
    source: str | Path,
    target_pdf: str | Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> TransferInspection:
    if _is_notewise(source):
        return inspect_notewise_transfer(source, target_pdf, progress=progress)
    return inspect_sdocx_transfer(source, target_pdf, progress=progress)


def preview_transfer(
    source: str | Path,
    target_pdf: str | Path,
    page_index: int,
    inspection: TransferInspection | None = None,
    *,
    source_index_override: int = -2,
) -> tuple[bytes, bytes, bytes, int]:
    preview = preview_notewise_transfer if _is_notewise(source) else preview_sdocx_transfer
    return preview(
        source,
        target_pdf,
        page_index,
        inspection,
        source_index_override=source_index_override,
    )


def transfer_handwriting(
    source: str | Path,
    target_pdf: str | Path,
    output: str | Path,
    *,
    match_override=None,
) -> dict:
    transfer = transfer_notewise_handwriting if _is_notewise(source) else transfer_sdocx_handwriting
    return transfer(source, target_pdf, output, match_override=match_override)


def output_suffix(source: str | Path) -> str:
    """결과 파일은 원본과 같은 형식으로 저장한다."""
    return ".notewise" if _is_notewise(source) else ".sdocx"


def with_output_suffix(name: str, source: str | Path) -> str:
    """저장할 이름에 원본 형식의 확장자를 붙인다.

    ``Path.with_suffix`` 는 마지막 마침표 뒤를 무조건 확장자로 보므로 ``강의자료 v1.2`` 같은
    이름의 뒷부분을 잘라 버린다. 그래서 알려진 필기 확장자일 때만 갈아 끼우고, 그 밖에는
    그대로 두고 뒤에 붙인다.
    """
    lowered = name.lower()
    for known in SUPPORTED_SUFFIXES:
        if lowered.endswith(known):
            return name[: -len(known)] + output_suffix(source)
    return name + output_suffix(source)


__all__ = [
    "HandwritingTransferError",
    "SUPPORTED_SUFFIXES",
    "TransferInspection",
    "inspect_transfer",
    "output_suffix",
    "preview_transfer",
    "transfer_handwriting",
    "with_output_suffix",
]
