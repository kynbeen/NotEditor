"""Small pypdf-backed output surface used by the Android PDF adapter."""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from pypdf import PdfReader, PdfWriter, Transformation


PdfSource = str | Path | bytes | bytearray | BinaryIO


@dataclass(frozen=True)
class PageRect:
    x0: float
    y0: float
    x1: float
    y1: float

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


class OutputPage:
    def __init__(self, document: OutputDocument, index: int):
        self._document = document
        self.number = index

    @property
    def _page(self):
        return self._document._writer.pages[self.number]

    @property
    def rect(self) -> PageRect:
        box = self._page.cropbox
        unit = float(self._page.user_unit)
        width, height = float(box.width) * unit, float(box.height) * unit
        if self.rotation % 180:
            width, height = height, width
        return PageRect(0.0, 0.0, width, height)

    @property
    def rotation(self) -> int:
        return int(self._page.rotation or 0)

    def set_mediabox(self, rect) -> None:
        from pypdf.generic import RectangleObject

        self._page.mediabox = RectangleObject(tuple(float(value) for value in rect))
        for name in ("/CropBox", "/BleedBox", "/TrimBox", "/ArtBox"):
            self._page.pop(name, None)

    def show_pdf_page(
        self,
        destination,
        source: PdfSource,
        page_index: int,
        *,
        keep_proportion: bool = True,
        clip=None,
    ) -> None:
        if clip is not None:
            raise NotImplementedError("Android PDF 출력은 clip 영역 지정을 지원하지 않습니다.")

        reader = self._document._reader(source)
        source_writer = PdfWriter()
        source_page = source_writer.add_page(reader.pages[page_index])
        if source_page.rotation:
            source_page.transfer_rotation_to_content()

        source_box = source_page.cropbox
        source_width = float(source_box.width)
        source_height = float(source_box.height)
        target_width = float(destination.width)
        target_height = float(destination.height)
        if min(source_width, source_height, target_width, target_height) <= 0:
            raise ValueError("PDF 쪽 크기는 0보다 커야 합니다.")

        if keep_proportion:
            scale_x = scale_y = min(target_width / source_width, target_height / source_height)
        else:
            scale_x = target_width / source_width
            scale_y = target_height / source_height

        placed_width = source_width * scale_x
        placed_height = source_height * scale_y
        target_box = self._page.cropbox
        unit = float(self._page.user_unit)
        translate_x = float(target_box.left) + (float(destination.x0) + (target_width - placed_width) / 2) / unit
        # MuPDF rectangles use a top-left origin; PDF content uses bottom-left.
        translate_y = float(target_box.top) - (float(destination.y1) - (target_height - placed_height) / 2) / unit
        operation = (Transformation().translate(-float(source_box.left), -float(source_box.bottom))
                     .scale(scale_x / unit, scale_y / unit).translate(translate_x, translate_y))
        self._page.merge_transformed_page(source_page, operation, expand=False)


class OutputDocument:
    def __init__(self):
        self._writer = PdfWriter()
        self._readers: list[PdfReader] = []

    @property
    def page_count(self) -> int:
        return len(self._writer.pages)

    def __getitem__(self, index: int) -> OutputPage:
        if index < 0 or index >= self.page_count:
            raise IndexError(f"페이지 인덱스 초과: {index}")
        return OutputPage(self, index)

    def _reader(self, source: PdfSource) -> PdfReader:
        if isinstance(source, (bytes, bytearray)):
            source = io.BytesIO(bytes(source))
        reader = PdfReader(source)
        if reader.is_encrypted:
            raise ValueError("암호화된 PDF는 합칠 수 없습니다.")
        self._readers.append(reader)
        return reader

    def insert_pdf(self, source: PdfSource, from_page: int = 0, to_page: int = -1) -> None:
        reader = self._reader(source)
        last = len(reader.pages) - 1 if to_page < 0 else to_page
        if from_page < 0 or last < from_page or last >= len(reader.pages):
            raise IndexError("합칠 PDF의 페이지 범위가 올바르지 않습니다.")
        for index in range(from_page, last + 1):
            self._writer.add_page(reader.pages[index])

    def new_page(self, width: float = 595, height: float = 842) -> OutputPage:
        self._writer.add_blank_page(width=float(width), height=float(height))
        return self[self.page_count - 1]

    def tobytes(self) -> bytes:
        buffer = io.BytesIO()
        self._writer.write(buffer)
        return buffer.getvalue()

    def save(self, filename: str | Path) -> None:
        with Path(filename).open("wb") as output:
            self._writer.write(output)
