from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .engine import ComposerSession, PdfComposerError
from .ranges import PageRangeError, parse_page_ranges


class ComposerApi:
    """Small JSON-friendly bridge exposed to the embedded web UI."""

    def __init__(self, session: ComposerSession | None = None) -> None:
        self.session = session or ComposerSession()
        self.window: Any | None = None
        self._closed = False

    def bind_window(self, window: Any) -> None:
        self.window = window

    @staticmethod
    def _ok(**payload: Any) -> dict:
        return {"ok": True, **payload}

    @staticmethod
    def _error(exc: Exception) -> dict:
        return {"ok": False, "error": str(exc)}

    def choose_pdfs(self) -> dict:
        try:
            if self.window is None:
                raise PdfComposerError("앱 창이 아직 준비되지 않았습니다.")
            import webview

            paths = self.window.create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=True,
                file_types=("PDF 문서 (*.pdf)",),
            )
            return self.add_paths(list(paths or []))
        except Exception as exc:
            return self._error(exc)

    def add_paths(self, paths: list[str]) -> dict:
        try:
            added = self.session.add_files(paths)
            return self._ok(
                added=added,
                sources=[source.as_dict() for source in self.session.sources],
            )
        except Exception as exc:
            return self._error(exc)

    def remove_document(self, document_id: str) -> dict:
        try:
            self.session.remove_source(document_id)
            return self._ok()
        except Exception as exc:
            return self._error(exc)

    def page_image(self, document_id: str, page_index: int, kind: str) -> dict:
        try:
            image = self.session.page_image(document_id, int(page_index), kind)
            return self._ok(image=image)
        except Exception as exc:
            return self._error(exc)

    def parse_range(self, text: str, page_count: int) -> dict:
        try:
            indices = parse_page_ranges(text, int(page_count))
            return self._ok(indices=indices)
        except (PageRangeError, ValueError) as exc:
            return self._error(exc)

    def save_result(self, order: list[dict], suggested_name: str = "조합된 문서.pdf") -> dict:
        try:
            if self.window is None:
                raise PdfComposerError("앱 창이 아직 준비되지 않았습니다.")
            import webview

            safe_name = re.sub(r'[<>:"/\\|?*]+', "_", suggested_name).strip(" .")
            if not safe_name.lower().endswith(".pdf"):
                safe_name += ".pdf"
            paths = self.window.create_file_dialog(
                webview.FileDialog.SAVE,
                save_filename=safe_name or "조합된 문서.pdf",
                file_types=("PDF 문서 (*.pdf)",),
            )
            if not paths:
                return self._ok(cancelled=True)
            output_path = paths if isinstance(paths, str) else paths[0]
            result = self.session.build_pdf(order, output_path)
            return self._ok(cancelled=False, result=result)
        except Exception as exc:
            return self._error(exc)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.session.close()


def run(debug: bool = False) -> None:
    import webview

    api = ComposerApi()
    static_file = Path(__file__).with_name("static") / "index.html"
    window = webview.create_window(
        "PDF 페이지 조합기",
        static_file.resolve().as_uri(),
        js_api=api,
        width=1440,
        height=900,
        min_size=(1080, 680),
        background_color="#0b1020",
        text_select=True,
    )
    api.bind_window(window)
    window.events.closed += api.close
    icon = Path(__file__).parents[1] / "assets" / "icon.ico"
    webview.start(
        debug=debug,
        private_mode=True,
        gui="edgechromium",
        icon=str(icon) if icon.exists() else None,
    )
