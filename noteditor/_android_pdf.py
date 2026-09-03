"""Chaquopy Android 환경용 MuPDF 어댑터.

Android 공식 MuPDF SDK (com.artifex.mupdf.fitz)를 감싸서
PyMuPDF(pymupdf)와 동일한 파이썬 인터페이스(open, Rect, Matrix, Point, Document, Page 등)를 제공합니다.
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

csGRAY = 1
csRGB = 2


class FileDataError(Exception):
    pass


class Point:
    def __init__(self, x: float = 0.0, y: float = 0.0):
        self.x = float(x)
        self.y = float(y)

    def __iter__(self):
        yield self.x
        yield self.y

    def __repr__(self):
        return f"Point({self.x}, {self.y})"


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

    def __repr__(self):
        return f"Rect({self.x0}, {self.y0}, {self.x1}, {self.y1})"


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
        for method in ("asPNG", "getPNGImage", "pngData"):
            if hasattr(self._raw, method):
                return bytes(getattr(self._raw, method)())
        if hasattr(self._raw, "getPixels"):
            from PIL import Image
            w = int(self._raw.getWidth())
            h = int(self._raw.getHeight())
            pixels = bytes(self._raw.getPixels())
            img = Image.frombytes("RGBA", (w, h), pixels)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        raise ValueError(f"지원하지 않는 포맷 또는 PNG 변환 불가: {output_format}")


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

    def get_pixmap(
        self,
        matrix: Matrix | None = None,
        colorspace: Any = None,
        alpha: bool = False,
        annots: bool = True,
    ) -> Pixmap:
        if matrix is not None:
            fitz_mat = _FitzMatrix(matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f)
        else:
            fitz_mat = _FitzMatrix(1.0, 1.0)

        cs = getattr(_FitzColorSpace, "DeviceRGB", None)
        if callable(cs):
            try:
                cs = cs()
            except Exception:
                pass
        if cs is None:
            getter = getattr(_FitzColorSpace, "getDeviceRGB", None)
            if getter:
                cs = getter()

        # MuPDF Android Pixmap.getPixels requires alpha=True (RGBA)
        alpha = True

        try:
            raw = self._raw.toPixmap(fitz_mat, cs, alpha, annots)
        except Exception:
            try:
                raw = self._raw.toPixmap(fitz_mat, cs, alpha)
            except Exception:
                raw = self._raw.toPixmap(fitz_mat, cs)
        return Pixmap(raw)

    def get_text(self, option: str = "text") -> Any:
        if option == "words":
            # (x0, y0, x1, y1, word, block_no, line_no, word_no)
            words = []
            return words
        try:
            return str(self._raw.toStructuredText().asJSON())
        except Exception:
            return ""

    def set_mediabox(self, rect: Rect) -> None:
        if hasattr(self._raw, "setMediaBox"):
            self._raw.setMediaBox(_FitzRect(rect.x0, rect.y0, rect.x1, rect.y1))


class Document:
    def __init__(self, raw_doc, path: str | None = None):
        self._raw = raw_doc
        self.path = path
        self._pypdf_writer = None

    @property
    def page_count(self) -> int:
        if self._pypdf_writer is not None and len(self._pypdf_writer.pages) > 0:
            return len(self._pypdf_writer.pages)
        return int(self._raw.countPages()) if self._raw else 0

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
        return bool(self._raw.needsPassword()) if self._raw else False

    def get_toc(self, simple: bool = True) -> list:
        return []

    def insert_pdf(self, doc: Document, from_page: int = 0, to_page: int = -1, **kwargs) -> None:
        if to_page < 0:
            to_page = doc.page_count - 1

        if hasattr(self._raw, "graftPage") and hasattr(getattr(doc, "_raw", None), "countPages"):
            try:
                for p in range(from_page, to_page + 1):
                    self._raw.graftPage(int(self._raw.countPages()), doc._raw, p)
                return
            except Exception:
                pass

        try:
            import pypdf

            if self._pypdf_writer is None:
                self._pypdf_writer = pypdf.PdfWriter()

            src_path = getattr(doc, "path", None)
            if src_path and Path(src_path).exists():
                reader = pypdf.PdfReader(str(src_path))
                for p in range(from_page, to_page + 1):
                    self._pypdf_writer.add_page(reader.pages[p])
            else:
                buf = doc.tobytes()
                if buf:
                    reader = pypdf.PdfReader(io.BytesIO(buf))
                    for p in range(from_page, to_page + 1):
                        self._pypdf_writer.add_page(reader.pages[p])
        except Exception:
            pass

    def tobytes(self, garbage: int = 4, deflate: bool = True) -> bytes:
        if self._pypdf_writer is not None and len(self._pypdf_writer.pages) > 0:
            buf = io.BytesIO()
            self._pypdf_writer.write(buf)
            return buf.getvalue()
        if hasattr(self._raw, "saveToBuffer"):
            try:
                return bytes(self._raw.saveToBuffer())
            except Exception:
                pass
        return b""

    def save(self, filename: str | Path, **kwargs) -> None:
        path = str(filename)
        if self._pypdf_writer is not None and len(self._pypdf_writer.pages) > 0:
            with open(path, "wb") as f:
                self._pypdf_writer.write(f)
            return

        if hasattr(self._raw, "save"):
            try:
                self._raw.save(path, "garbage=4,deflate")
                if Path(path).exists() and Path(path).stat().st_size > 300:
                    return
            except Exception:
                pass
            try:
                self._raw.save(path)
                if Path(path).exists() and Path(path).stat().st_size > 300:
                    return
            except Exception:
                pass

        buf = self.tobytes()
        if buf:
            with open(path, "wb") as f:
                f.write(buf)

    def close(self) -> None:
        if hasattr(self._raw, "destroy"):
            try:
                self._raw.destroy()
            except Exception:
                pass

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
            try:
                raw = _FitzPDFDocument()
            except Exception:
                raw = None
            return Document(raw)
        path = str(source) if isinstance(source, (str, Path)) else str(source)
        raw = _FitzDocument.openDocument(path)
        return Document(raw, path=path)
    except Exception as exc:
        raise FileDataError(f"PDF를 열 수 없습니다: {exc}") from exc


# PyMuPDF 표준 별칭 매핑
open = open_pdf
