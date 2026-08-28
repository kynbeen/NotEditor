"""Dispatch handwriting transfers by note-file format."""
from __future__ import annotations

from pathlib import Path

from .notewise_transfer import (
    NotewiseTransferError,
    inspect_notewise_transfer,
    preview_notewise_transfer,
    transfer_notewise_handwriting,
)
from .sdocx_transfer import (
    SdocxTransferError,
    inspect_transfer as inspect_sdocx_transfer,
    preview_transfer as preview_sdocx_transfer,
    transfer_handwriting as transfer_sdocx_handwriting,
)


HandwritingTransferError = (SdocxTransferError, NotewiseTransferError)


def _suffix(source: str | Path) -> str:
    return Path(source).suffix.lower()


def inspect_transfer(source: str | Path, target_pdf: str | Path):
    if _suffix(source) == ".notewise":
        return inspect_notewise_transfer(source, target_pdf)
    return inspect_sdocx_transfer(source, target_pdf)


def preview_transfer(source: str | Path, target_pdf: str | Path, page_index: int,
                     inspection=None, *, source_index_override: int = -2):
    if _suffix(source) == ".notewise":
        return preview_notewise_transfer(
            source,
            target_pdf,
            page_index,
            inspection,
            source_index_override=source_index_override,
        )
    return preview_sdocx_transfer(
        source,
        target_pdf,
        page_index,
        inspection,
        source_index_override=source_index_override,
    )


def transfer_handwriting(source: str | Path, target_pdf: str | Path,
                         output: str | Path, *, match_override=None):
    if _suffix(source) == ".notewise":
        return transfer_notewise_handwriting(
            source, target_pdf, output, match_override=match_override
        )
    return transfer_sdocx_handwriting(
        source, target_pdf, output, match_override=match_override
    )
