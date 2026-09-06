"""PyMuPDF-compatible subset for Chaquopy Android builds.

Android's PdfRenderer handles reading and rasterization. The permissively
licensed pypdf output layer handles copying, blank pages, and transformations.
"""
from __future__ import annotations

import io
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from java import jclass
from PIL import Image

from .pypdf_output import OutputDocument, OutputPage

_ParcelFileDescriptor = jclass("android.os.ParcelFileDescriptor")
_PdfRenderer = jclass("android.graphics.pdf.PdfRenderer")
_PdfRendererPage = jclass("android.graphics.pdf.PdfRenderer$Page")
_Bitmap = jclass("android.graphics.Bitmap")
_BitmapConfig = jclass("android.graphics.Bitmap$Config")
_BitmapCompressFormat = jclass("android.graphics.Bitmap$CompressFormat")
_AndroidMatrix = jclass("android.graphics.Matrix")
_Color = jclass("android.graphics.Color")
_ByteArrayOutputStream = jclass("java.io.ByteArrayOutputStream")
_JavaFile = jclass("java.io.File")

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

    @property
    def is_empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    @property
    def is_infinite(self) -> bool:
        return not all(math.isfinite(value) for value in self)

    def __iter__(self):
        yield self.x0
        yield self.y0
        yield self.x1
        yield self.y1

    def __repr__(self):
        return f"Rect({self.x0}, {self.y0}, {self.x1}, {self.y1})"


class Matrix:
    def __init__(
        self,
        a: float,
        b: float | None = None,
        c: float = 0.0,
        d: float | None = None,
        e: float = 0.0,
        f: float = 0.0,
    ):
        if d is None:
            self.a = float(a)
            self.b = 0.0
            self.c = 0.0
            self.d = float(a if b is None else b)
            self.e = 0.0
            self.f = 0.0
        else:
            self.a = float(a)
            self.b = float(b or 0.0)
            self.c = float(c)
            self.d = float(d)
            self.e = float(e)
            self.f = float(f)


class Pixmap:
    def __init__(
        self,
        png: bytes,
        *,
        colorspace: Any = None,
        alpha: bool = False,
        x: int = 0,
        y: int = 0,
    ):
        image = Image.open(io.BytesIO(png))
        mode = "RGBA" if alpha else ("L" if colorspace == csGRAY else "RGB")
        self._image = image.convert(mode)
        self.width, self.height = self._image.size
        self.samples = self._image.tobytes()
        self.x = x
        self.y = y

    def tobytes(self, output_format: str = "png") -> bytes:
        if output_format.lower() != "png":
            raise ValueError(f"지원하지 않는 이미지 포맷: {output_format}")
        buffer = io.BytesIO()
        self._image.save(buffer, format="PNG")
        return buffer.getvalue()


class Page:
    def __init__(self, raw_page, index: int, document: Document, output_page: OutputPage | None = None):
        self._raw = raw_page
        self.number = index
        self._document = document
        self._output_page = output_page

    @property
    def rect(self) -> Rect:
        if self._output_page is not None:
            return Rect(*self._output_page.rect)
        return Rect(0, 0, self._raw.getWidth(), self._raw.getHeight())

    @property
    def rotation(self) -> int:
        return self._output_page.rotation if self._output_page is not None else 0

    def get_pixmap(
        self,
        matrix: Matrix | None = None,
        colorspace: Any = None,
        alpha: bool = False,
        annots: bool = True,
        clip: Rect | None = None,
    ) -> Pixmap:
        if self._output_page is not None:
            return self._document._render_output_page(
                self.number,
                matrix=matrix,
                colorspace=colorspace,
                alpha=alpha,
                annots=annots,
                clip=clip,
            )
        scale_x = matrix.a if matrix is not None else 1.0
        scale_y = matrix.d if matrix is not None else 1.0
        return _render_page(
            self._raw,
            scale_x,
            scale_y,
            colorspace=colorspace,
            alpha=alpha,
            clip=clip,
        )

    def get_text(self, option: str = "text") -> Any:
        if self._output_page is not None:
            return [] if option == "words" else ""
        return self._document._extract_text(self.number, words=option == "words")

    def set_mediabox(self, rect: Rect) -> None:
        if self._output_page is None:
            raise NotImplementedError("Android에서는 열린 PDF의 미디어 박스를 직접 바꿀 수 없습니다.")
        self._output_page.set_mediabox(rect)

    def show_pdf_page(
        self,
        destination: Rect,
        source_document: Document,
        page_index: int,
        *,
        keep_proportion: bool = True,
        clip=None,
    ) -> None:
        if self._output_page is None:
            raise TypeError("새로 만든 출력 페이지에만 PDF 페이지를 배치할 수 있습니다.")
        self._output_page.show_pdf_page(
            destination,
            source_document._pypdf_source(),
            page_index,
            keep_proportion=keep_proportion,
            clip=clip,
        )


class Document:
    def __init__(
        self,
        renderer=None,
        *,
        descriptor=None,
        source=None,
        temporary_path: Path | None = None,
        output: OutputDocument | None = None,
    ):
        self._renderer = renderer
        self._descriptor = descriptor
        self._source = source
        self._temporary_path = temporary_path
        self._output = output
        self._active_page = None

    @property
    def page_count(self) -> int:
        return self._output.page_count if self._output is not None else int(self._renderer.getPageCount())

    def __len__(self) -> int:
        return self.page_count

    def __getitem__(self, index: int) -> Page:
        if index < 0 or index >= self.page_count:
            raise IndexError(f"페이지 인덱스 초과: {index}")
        if self._output is not None:
            return Page(None, index, self, output_page=self._output[index])
        if self._active_page is not None:
            self._active_page.close()
        self._active_page = self._renderer.openPage(index)
        return Page(self._active_page, index, self)

    def __iter__(self):
        for index in range(self.page_count):
            yield self[index]

    @property
    def needs_pass(self) -> bool:
        return False

    def get_toc(self, simple: bool = True) -> list:
        return []

    def insert_pdf(self, doc: Document, from_page: int = 0, to_page: int = -1, **kwargs) -> None:
        if self._output is None:
            raise TypeError("새 출력 문서에만 PDF 페이지를 추가할 수 있습니다.")
        self._output.insert_pdf(doc._pypdf_source(), from_page=from_page, to_page=to_page)

    def new_page(self, width: float = 595, height: float = 842) -> Page:
        if self._output is None:
            raise TypeError("새 출력 문서에만 페이지를 추가할 수 있습니다.")
        output_page = self._output.new_page(width=width, height=height)
        return Page(None, output_page.number, self, output_page=output_page)

    def _pypdf_source(self):
        if self._output is not None:
            return self._output.tobytes()
        if isinstance(self._source, (Path, bytes)):
            return self._source
        raise FileDataError("PDF 원본 바이트를 찾을 수 없습니다.")

    def _extract_text(self, index: int, *, words: bool):
        from pypdf import PdfReader

        text = PdfReader(self._pypdf_source()).pages[index].extract_text() or ""
        if not words:
            return text
        # pypdf does not expose PyMuPDF-compatible word boxes. Visual matching
        # remains the authoritative Android page fingerprint.
        return []

    def _render_output_page(self, index: int, **kwargs) -> Pixmap:
        temporary = _temporary_pdf(self._output.tobytes())
        try:
            renderer, descriptor = _open_renderer(temporary)
            try:
                page = renderer.openPage(index)
                try:
                    matrix = kwargs.get("matrix")
                    return _render_page(
                        page,
                        matrix.a if matrix is not None else 1.0,
                        matrix.d if matrix is not None else 1.0,
                        colorspace=kwargs.get("colorspace"),
                        alpha=kwargs.get("alpha", False),
                        clip=kwargs.get("clip"),
                    )
                finally:
                    page.close()
            finally:
                renderer.close()
                descriptor.close()
        finally:
            temporary.unlink(missing_ok=True)

    def tobytes(self, garbage: int = 4, deflate: bool = True) -> bytes:
        if self._output is not None:
            return self._output.tobytes()
        source = self._pypdf_source()
        return source.read_bytes() if isinstance(source, Path) else bytes(source)

    def save(self, filename: str | Path, **kwargs) -> None:
        if self._output is None:
            Path(filename).write_bytes(self.tobytes())
        else:
            self._output.save(filename)

    def close(self) -> None:
        if self._active_page is not None:
            self._active_page.close()
            self._active_page = None
        if self._renderer is not None:
            self._renderer.close()
            self._renderer = None
        if self._descriptor is not None:
            self._descriptor.close()
            self._descriptor = None
        if self._temporary_path is not None:
            self._temporary_path.unlink(missing_ok=True)
            self._temporary_path = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def _temporary_pdf(payload: bytes) -> Path:
    descriptor, name = tempfile.mkstemp(prefix="noteditor-", suffix=".pdf")
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)
    return Path(name)


def _open_renderer(path: Path):
    descriptor = _ParcelFileDescriptor.open(_JavaFile(str(path)), _ParcelFileDescriptor.MODE_READ_ONLY)
    try:
        return _PdfRenderer(descriptor), descriptor
    except Exception:
        descriptor.close()
        raise


def _render_page(
    raw_page,
    scale_x: float,
    scale_y: float,
    *,
    colorspace=None,
    alpha=False,
    clip: Rect | None = None,
) -> Pixmap:
    page_rect = Rect(0, 0, raw_page.getWidth(), raw_page.getHeight())
    area = clip or page_rect
    width = max(1, round(area.width * scale_x))
    height = max(1, round(area.height * scale_y))
    bitmap = _Bitmap.createBitmap(width, height, _BitmapConfig.ARGB_8888)
    try:
        bitmap.eraseColor(_Color.WHITE)
        transform = _AndroidMatrix()
        transform.setValues([
            float(scale_x), 0.0, float(-area.x0 * scale_x),
            0.0, float(scale_y), float(-area.y0 * scale_y),
            0.0, 0.0, 1.0,
        ])
        raw_page.render(bitmap, None, transform, _PdfRendererPage.RENDER_MODE_FOR_DISPLAY)
        output = _ByteArrayOutputStream()
        try:
            if not bitmap.compress(_BitmapCompressFormat.PNG, 100, output):
                raise FileDataError("Android PDF 페이지를 PNG로 만들 수 없습니다.")
            return Pixmap(
                bytes(output.toByteArray()),
                colorspace=colorspace,
                alpha=alpha,
                x=round(area.x0 * scale_x),
                y=round(area.y0 * scale_y),
            )
        finally:
            output.close()
    finally:
        bitmap.recycle()


def open_pdf(source=None, stream: bytes | None = None, filetype: str | None = None) -> Document:
    if source is None and stream is None:
        return Document(output=OutputDocument())

    temporary = None
    try:
        if stream is not None:
            payload = bytes(stream)
            temporary = _temporary_pdf(payload)
            path = temporary
            stored_source = payload
        else:
            path = Path(source)
            stored_source = path
        renderer, descriptor = _open_renderer(path)
        return Document(
            renderer,
            descriptor=descriptor,
            source=stored_source,
            temporary_path=temporary,
        )
    except Exception as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise FileDataError(f"PDF를 열 수 없습니다: {exc}") from exc


open = open_pdf
