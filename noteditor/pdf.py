"""PDF 엔진 추상화 계층.

데스크톱/서버(Windows, Linux, macOS)에서는 고속 ``pymupdf`` (C 확장)를 사용하고,
안드로이드(Chaquopy) 환경에서는 공식 Android MuPDF AAR(``com.artifex.mupdf.fitz``)을
연동하여 동일한 PDF 조작 및 렌더링 기능을 제공합니다.
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


def __getattr__(name: str):
    """지정되지 않은 모든 fitz/pymupdf 속성을 활성 백엔드로 투명하게 전달한다."""
    return getattr(_backend, name)


__all__ = ["open", "Rect", "Matrix", "FileDataError", "csGRAY", "csRGB"]
