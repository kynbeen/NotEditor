"""PDF 엔진 추상화 계층.

데스크톱/서버(Windows, Linux, macOS)에서는 ``pymupdf``를 사용하고, Android에서는
플랫폼 ``PdfRenderer``와 순수 Python ``pypdf``를 조합한 호환 계층을 사용한다.
"""
from __future__ import annotations

import os
import sys

_IS_ANDROID = (
    "ANDROID_BOOTLOGO" in os.environ
    or "ANDROID_ROOT" in os.environ
    or "com.chaquo.python" in sys.modules
    or os.environ.get("NOTEDITOR_PLATFORM") == "android"
)

if _IS_ANDROID:
    try:
        from . import _android_pdf as _backend
    except ImportError:
        import pymupdf as _backend
else:
    import pymupdf as _backend

open = _backend.open
Rect = _backend.Rect
Matrix = _backend.Matrix
FileDataError = getattr(_backend, "FileDataError", Exception)
csGRAY = getattr(_backend, "csGRAY", None)
csRGB = getattr(_backend, "csRGB", None)
Point = getattr(_backend, "Point", None)


def show_pdf_page(page, destination, source, page_index: int) -> None:
    """Place the displayed page without changing the caller's source document."""
    rotation = source[page_index].rotation
    if not rotation:
        page.show_pdf_page(destination, source, page_index, keep_proportion=True, clip=None)
        return
    # MuPDF's placement coordinates exclude /Rotate. Copy one page and apply
    # its display rotation explicitly, preserving the original crop box.
    with open() as unrotated:
        unrotated.insert_pdf(source, from_page=page_index, to_page=page_index)
        unrotated[0].set_rotation(0)
        page.show_pdf_page(destination, unrotated, 0, keep_proportion=True, rotate=-rotation)


def __getattr__(name: str):
    """지정되지 않은 모든 fitz/pymupdf 속성을 활성 백엔드로 투명하게 전달한다."""
    return getattr(_backend, name)


__all__ = ["open", "Rect", "Matrix", "Point", "FileDataError", "csGRAY", "csRGB", "show_pdf_page"]

