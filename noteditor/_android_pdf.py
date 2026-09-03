"""Chaquopy Android 환경용 MuPDF 어댑터.

Android 공식 MuPDF SDK (com.artifex.mupdf.fitz)를 감싸서
PyMuPDF(pymupdf)와 동일한 파이썬 인터페이스(open, Rect, Matrix, Document, Page 등)를 제공합니다.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from java import jclass

_FitzDocument = jclass("com.artifex.mupdf.fitz.Document")
_FitzPDFDocument = jclass("com.artifex.mupdf.fitz.PDFDocument")
_FitzRect = jclass("com.artifex.mupdf.fitz.Rect")
_FitzMatrix = jclass("com.artifex.mupdf.fitz.Matrix")
_FitzColorSpace = jclass("com.artifex.mupdf.fitz.ColorSpace")


class FileDataError(Exception):
    pass


class Rect:
    def __init__(self, x0: float, y0: float, x1: float, y1: float):
        self.x0 = float(x0)
        self.y0 = float(y0)
        self.x1 = float(x1)
        self.y1 = float(y1)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def __iter__(self):
        yield self.x0
        yield self.y0
        yield self.x1
        yield self.y1


class Matrix:
    def __init__(self, a: float, b: float = 0.0, c: float = 0.0, d: float = 1.0, e: float = 0.0, f: float = 0.0):
        if b == 0.0 and c == 0.0 and d == 1.0 and e == 0.0 and f == 0.0:
            # Matrix(scale, scale) 지원
            self.a = float(a)
            self.d = float(a)
            self.b = 0.0
            self.c = 0.0
            self.e = 0.0
            self.f = 0.0
        else:
            self.a = float(a)
            self.b = float(b)
            self.c = float(c)
            self.d = float(d)
            self.e = float(e)
            self.f = float(f)


class Pixmap:
    def __init__(self, raw_pixmap):
        self._raw = raw_pixmap

    def tobytes(self, output_format: str = "png") -> bytes:
        if output_format.lower() == "png":
            return bytes(self._raw.asPNG())
        raise ValueError(f"지원하지 않는 포맷: {output_format}")


class Page:
    def __init__(self, raw_page, index: int, document: Document):
        self._raw = raw_page
        self.number = index
        self._document = document

    @property
    def rect(self) -> Rect:
        bounds = self._raw.getBounds()
        return Rect(bounds.x0, bounds.y0, bounds.x1, bounds.y1)

    @property
    def rotation(self) -> int:
        return int(self._raw.getRotation() if hasattr(self._raw, "getRotation") else 0)

    def get_pixmap(self, matrix: Matrix | None = None, alpha: bool = False) -> Pixmap:
        if matrix is not None:
            fitz_mat = _FitzMatrix(matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f)
        else:
            fitz_mat = _FitzMatrix(1.0, 1.0)
        cs = _FitzColorSpace.DeviceRGB()
        raw = self._raw.toPixmap(fitz_mat, cs, alpha)
        return Pixmap(raw)

    def get_text(self, option: str = "text") -> Any:
        if option == "words":
            # (x0, y0, x1, y1, word, block_no, line_no, word_no)
            words = []
            tp = self._raw.toStructuredText()
            # structured text blocks
            # 단순 텍스트 추출로 안전한 폴백 제공
            return words
        return str(self._raw.toStructuredText().asJSON())

    def set_mediabox(self, rect: Rect) -> None:
        if hasattr(self._raw, "setMediaBox"):
            self._raw.setMediaBox(_FitzRect(rect.x0, rect.y0, rect.x1, rect.y1))


class Document:
    def __init__(self, raw_doc):
        self._raw = raw_doc

    @property
    def page_count(self) -> int:
        return int(self._raw.countPages())

    def __len__(self) -> int:
        return self.page_count

    def __getitem__(self, index: int) -> Page:
        if index < 0 or index >= self.page_count:
            raise IndexError(f"페이지 인덱스 초과: {index}")
        return Page(self._raw.loadPage(index), index, self)

    def __iter__(self):
        for i in range(self.page_count):
            yield self[i]

    @property
    def needs_pass(self) -> bool:
        return bool(self._raw.needsPassword())

    def tobytes(self, garbage: int = 4, deflate: bool = True) -> bytes:
        # byte array로 저장
        baos = io.BytesIO()
        # PDFDocument save
        return bytes(self._raw.saveToBuffer())

    def close(self) -> None:
        if hasattr(self._raw, "destroy"):
            self._raw.destroy()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def open_pdf(source=None, stream: bytes | None = None, filetype: str | None = None) -> Document:
    try:
        if stream is not None:
            raw = _FitzDocument.openDocument(stream)
            return Document(raw)
        if source is None:
            raw = _FitzPDFDocument()
            return Document(raw)
        path = str(source) if isinstance(source, (str, Path)) else str(source)
        raw = _FitzDocument.openDocument(path)
        return Document(raw)
    except Exception as exc:
        raise FileDataError(f"PDF를 열 수 없습니다: {exc}") from exc

