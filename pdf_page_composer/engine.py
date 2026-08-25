from __future__ import annotations

import base64
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class PdfComposerError(RuntimeError):
    pass


class EncryptedPdfError(PdfComposerError):
    pass


@dataclass(frozen=True)
class PageRef:
    document_id: str
    page_index: int

    @classmethod
    def from_value(cls, value: dict) -> "PageRef":
        try:
            document_id = str(value["document_id"])
            page_index = int(value["page_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PdfComposerError("결과 페이지 정보가 올바르지 않습니다.") from exc
        return cls(document_id, page_index)


@dataclass
class SourceDocument:
    id: str
    path: Path
    name: str
    page_count: int
    pages: list[dict]
    has_forms: bool
    has_outlines: bool
    has_attachments: bool
    has_signatures: bool

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "page_count": self.page_count,
            "pages": self.pages,
            "has_forms": self.has_forms,
            "has_outlines": self.has_outlines,
            "has_attachments": self.has_attachments,
            "has_signatures": self.has_signatures,
        }


def _has_key(container, key: str) -> bool:
    try:
        return key in container
    except (TypeError, ValueError):
        return False


class ComposerSession:
    """Owns source paths and preview caches for one process-local app session."""

    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="pdf-page-composer-")
        self.temp_dir = Path(self._temporary.name)
        self._sources: dict[str, SourceDocument] = {}
        self._source_order: list[str] = []
        self._preview_cache: dict[tuple[str, int, str], str] = {}
        self._lock = threading.RLock()

    @property
    def sources(self) -> list[SourceDocument]:
        return [self._sources[source_id] for source_id in self._source_order]

    def close(self) -> None:
        with self._lock:
            self._preview_cache.clear()
            self._sources.clear()
            self._source_order.clear()
            self._temporary.cleanup()

    def add_files(self, paths: Iterable[str | Path]) -> list[dict]:
        staged: list[SourceDocument] = []
        known = {source.path for source in self._sources.values()}
        for raw_path in paths:
            path = Path(raw_path).expanduser().resolve()
            if path in known:
                continue
            source = self._inspect_source(path)
            staged.append(source)
            known.add(path)

        added: list[dict] = []
        for source in staged:
            self._sources[source.id] = source
            self._source_order.append(source.id)
            added.append(source.as_dict())
        return added

    def remove_source(self, source_id: str) -> None:
        with self._lock:
            if source_id not in self._sources:
                raise PdfComposerError("이미 제거되었거나 알 수 없는 PDF입니다.")
            del self._sources[source_id]
            self._source_order.remove(source_id)
            for key in [key for key in self._preview_cache if key[0] == source_id]:
                del self._preview_cache[key]

    def _inspect_source(self, path: Path) -> SourceDocument:
        if path.suffix.lower() != ".pdf" or not path.is_file():
            raise PdfComposerError(f"PDF 파일이 아닙니다: {path.name}")

        import pymupdf
        import pikepdf

        try:
            with pymupdf.open(path) as document:
                if document.needs_pass:
                    raise EncryptedPdfError(f"암호화된 PDF는 지원하지 않습니다: {path.name}")
                if document.page_count < 1:
                    raise PdfComposerError(f"페이지가 없는 PDF입니다: {path.name}")
                pages = []
                for index, page in enumerate(document):
                    rect = page.rect
                    pages.append({
                        "index": index,
                        "number": index + 1,
                        "width": round(float(rect.width), 2),
                        "height": round(float(rect.height), 2),
                        "rotation": int(page.rotation),
                    })
                has_outlines = bool(document.get_toc(simple=True))
        except EncryptedPdfError:
            raise
        except Exception as exc:
            raise PdfComposerError(f"PDF를 읽을 수 없습니다: {path.name} ({exc})") from exc

        try:
            with pikepdf.Pdf.open(path) as pdf:
                root = pdf.Root
                has_forms = _has_key(root, "/AcroForm")
                has_attachments = (
                    _has_key(root, "/Names")
                    and _has_key(root.Names, "/EmbeddedFiles")
                )
                has_signatures = False
                if has_forms:
                    for field in getattr(root.AcroForm, "Fields", []):
                        if str(field.get("/FT", "")) == "/Sig":
                            has_signatures = True
                            break
        except pikepdf.PasswordError as exc:
            raise EncryptedPdfError(f"암호화된 PDF는 지원하지 않습니다: {path.name}") from exc
        except Exception as exc:
            raise PdfComposerError(f"PDF 구조를 확인할 수 없습니다: {path.name} ({exc})") from exc

        return SourceDocument(
            id=uuid.uuid4().hex,
            path=path,
            name=path.name,
            page_count=len(pages),
            pages=pages,
            has_forms=has_forms,
            has_outlines=has_outlines,
            has_attachments=has_attachments,
            has_signatures=has_signatures,
        )

    def page_image(self, source_id: str, page_index: int, kind: str = "thumbnail") -> str:
        if kind not in {"thumbnail", "preview"}:
            raise PdfComposerError("알 수 없는 미리보기 크기입니다.")
        source = self._source(source_id)
        self._validate_page(source, page_index)
        key = (source_id, page_index, kind)
        with self._lock:
            cached = self._preview_cache.get(key)
            if cached:
                return cached

        import pymupdf

        max_side = 260 if kind == "thumbnail" else 1500
        with pymupdf.open(source.path) as document:
            page = document[page_index]
            scale = min(max_side / max(page.rect.width, page.rect.height), 2.5)
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, annots=True)
            encoded = base64.b64encode(pixmap.tobytes("png")).decode("ascii")
        value = "data:image/png;base64," + encoded
        with self._lock:
            self._preview_cache[key] = value
        return value

    def build_pdf(self, order: Iterable[dict], output_path: str | Path) -> dict:
        refs = [PageRef.from_value(item) for item in order]
        if not refs:
            raise PdfComposerError("선택된 페이지가 없습니다.")
        if len(set(refs)) != len(refs):
            raise PdfComposerError("같은 페이지가 결과에 두 번 들어가 있습니다.")

        for ref in refs:
            self._validate_page(self._source(ref.document_id), ref.page_index)

        output = Path(output_path).expanduser().resolve()
        if output.suffix.lower() != ".pdf":
            output = output.with_suffix(".pdf")
        output.parent.mkdir(parents=True, exist_ok=True)
        warnings = self._compose(refs, output)
        return {
            "path": str(output),
            "page_count": len(refs),
            "size": output.stat().st_size,
            "warnings": warnings,
        }

    def _compose(self, refs: list[PageRef], output: Path) -> list[str]:
        import pikepdf

        warnings: list[str] = []
        selected_by_source: dict[str, list[int]] = {source_id: [] for source_id in self._source_order}
        for ref in refs:
            selected_by_source[ref.document_id].append(ref.page_index)

        temporary_fd, temporary_name = tempfile.mkstemp(
            dir=output.parent, prefix=f".{output.stem}-", suffix=".tmp.pdf"
        )
        os.close(temporary_fd)
        temporary = Path(temporary_name)
        source_pdfs: list = []
        try:
            destination = pikepdf.Pdf.new()
            page_objects: dict[PageRef, object] = {}
            first_metadata: dict[str, str] = {}
            for source_id in self._source_order:
                page_indices = sorted(set(selected_by_source[source_id]))
                if not page_indices:
                    continue
                source = self._source(source_id)
                pdf = pikepdf.Pdf.open(source.path)
                source_pdfs.append(pdf)
                if not first_metadata:
                    first_metadata = {
                        str(key): str(value)
                        for key, value in pdf.docinfo.items()
                        if value is not None
                    }
                start = len(destination.pages)
                copy_result = destination.add_pages_from(
                    pdf, pages=page_indices, forms="preserve"
                )
                for offset, page_index in enumerate(page_indices):
                    page_objects[PageRef(source_id, page_index)] = destination.pages[start + offset]
                renamed = dict(getattr(copy_result, "renamed_fields", {}) or {})
                partial = list(getattr(copy_result, "partial_fields", []) or [])
                if renamed:
                    warnings.append(
                        f"{source.name}: 충돌한 양식 필드 {len(renamed)}개의 이름을 안전하게 변경했습니다."
                    )
                if partial:
                    warnings.append(
                        f"{source.name}: 선택 페이지 밖과 연결된 부분 양식 {len(partial)}개가 있습니다."
                    )
                if source.has_outlines:
                    warnings.append(f"{source.name}: 원본 문서 책갈피는 결과에 복사하지 않습니다.")
                if source.has_attachments:
                    warnings.append(f"{source.name}: 문서 첨부 파일은 결과에 복사하지 않습니다.")
                if source.has_signatures:
                    warnings.append(f"{source.name}: 기존 디지털 서명은 페이지 조합 후 유효하지 않습니다.")

            desired = [page_objects[ref] for ref in refs]
            for target_index, wanted in enumerate(desired):
                current_index = next(
                    index for index, page in enumerate(destination.pages)
                    if page.objgen == wanted.objgen
                )
                if current_index == target_index:
                    continue
                moved = destination.pages[current_index]
                del destination.pages[current_index]
                destination.pages.insert(target_index, moved)

            for key, value in first_metadata.items():
                if key not in {"/ModDate", "/CreationDate", "/Producer"}:
                    destination.docinfo[key] = value
            destination.docinfo["/Producer"] = "PDF Page Composer (pikepdf/qpdf)"
            destination.docinfo["/Subject"] = "Combined from: " + ", ".join(
                self._source(source_id).name
                for source_id in self._source_order
                if selected_by_source[source_id]
            )
            destination.save(
                temporary,
                object_stream_mode=pikepdf.ObjectStreamMode.preserve,
                normalize_content=False,
                recompress_flate=False,
            )
            destination.close()

            with pikepdf.Pdf.open(temporary) as check:
                if len(check.pages) != len(refs):
                    raise PdfComposerError("저장된 PDF의 페이지 수가 예상과 다릅니다.")
            os.replace(temporary, output)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            for pdf in source_pdfs:
                pdf.close()
        return warnings

    def _source(self, source_id: str) -> SourceDocument:
        try:
            return self._sources[source_id]
        except KeyError as exc:
            raise PdfComposerError("알 수 없거나 제거된 PDF입니다.") from exc

    @staticmethod
    def _validate_page(source: SourceDocument, page_index: int) -> None:
        if not 0 <= int(page_index) < source.page_count:
            raise PdfComposerError(
                f"{source.name}의 페이지 범위를 벗어났습니다: {int(page_index) + 1}"
            )
