from __future__ import annotations

import re
import logging
from pathlib import Path
from typing import Any

from . import __version__
from .engine import ComposerSession, PdfComposerError
from .ranges import PageRangeError, parse_page_ranges
from .handwriting_transfer import (
    inspect_transfer,
    output_suffix,
    preview_transfer,
    transfer_handwriting,
    with_output_suffix,
)

APP_USER_MODEL_ID = "NotEditor.Desktop"
MISSING_HANDWRITING_MESSAGE = "필기 원본과 대상 PDF를 모두 선택하세요."


def _png_data_uri(payload: bytes) -> str:
    import base64

    return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")


def configure_windows_app_identity() -> None:
    """Python 프로세스가 아니라 독립 앱으로 작업표시줄에 그룹화되게 한다."""
    import os

    if os.name != "nt":
        return
    try:
        import ctypes

        set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        result = set_app_id(APP_USER_MODEL_ID)
        if result != 0:
            logging.getLogger("noteditor").warning(
                "Failed to set AppUserModelID: HRESULT=%s", result
            )
    except Exception:
        logging.getLogger("noteditor").exception(
            "Failed to configure Windows app identity"
        )


class ComposerApi:
    """Small JSON-friendly bridge exposed to the embedded web UI."""

    def __init__(self, session: ComposerSession | None = None) -> None:
        # pywebview exposes every public attribute on js_api. Keep native and
        # stateful Python objects private or its serializer walks the complete
        # WinForms/WebView2 object graph and eventually recurses forever.
        self._session = session or ComposerSession()
        self._window: Any | None = None
        self._closed = False
        self._handwriting_source: Path | None = None
        self._handwriting_target: Path | None = None
        self._handwriting_cache: tuple | None = None

    def _bind_window(self, window: Any) -> None:
        self._window = window

    @staticmethod
    def _ok(**payload: Any) -> dict:
        return {"ok": True, **payload}

    @staticmethod
    def _error(exc: Exception) -> dict:
        logging.getLogger("noteditor").error(
            "Desktop API request failed: %s", exc, exc_info=exc
        )
        return {"ok": False, "error": str(exc)}

    def health(self) -> dict:
        return self._ok(version=__version__)

    def log_client_error(self, message: str) -> dict:
        logging.getLogger("noteditor").error("UI error: %s", message)
        return self._ok()

    def toggle_fullscreen(self) -> dict:
        try:
            if self._window is None:
                raise PdfComposerError("앱 창이 아직 준비되지 않았습니다.")
            self._window.toggle_fullscreen()
            return self._ok()
        except Exception as exc:
            return self._error(exc)

    def choose_pdfs(self) -> dict:
        try:
            if self._window is None:
                raise PdfComposerError("앱 창이 아직 준비되지 않았습니다.")
            import webview

            paths = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=True,
                file_types=("PDF 문서 (*.pdf)",),
            )
            return self.add_paths(list(paths or []))
        except Exception as exc:
            return self._error(exc)

    @staticmethod
    def _dialog_path(value: Any) -> Path | None:
        if not value:
            return None
        if isinstance(value, str):
            return Path(value).expanduser().resolve()
        return Path(value[0]).expanduser().resolve() if value else None

    def _inspection(self):
        """본문 정렬 추정은 문서 전체를 훑으므로 파일이 그대로면 결과를 다시 쓴다."""
        if not self._handwriting_source or not self._handwriting_target:
            raise PdfComposerError(MISSING_HANDWRITING_MESSAGE)
        key = (
            str(self._handwriting_source),
            str(self._handwriting_target),
            self._handwriting_source.stat().st_mtime_ns,
            self._handwriting_target.stat().st_mtime_ns,
        )
        cached = self._handwriting_cache
        if cached and cached[0] == key:
            return cached[1]
        inspection = inspect_transfer(self._handwriting_source, self._handwriting_target)
        self._handwriting_cache = (key, inspection)
        return inspection

    def _handwriting_status(self) -> dict:
        payload = {
            "source_name": self._handwriting_source.name if self._handwriting_source else None,
            "source_format": self._handwriting_source.suffix.lower().lstrip(".")
            if self._handwriting_source else None,
            "target_name": self._handwriting_target.name if self._handwriting_target else None,
            "ready": False,
            "inspection": None,
        }
        if self._handwriting_source and self._handwriting_target:
            payload.update(ready=True, inspection=self._inspection().as_dict())
        return payload

    def handwriting_preview(self, page_index: int = 0, source_index: int = -2) -> dict:
        try:
            inspection = self._inspection()
            index = max(0, min(int(page_index), inspection.page_count - 1))
            before, after, ink, stroke_count = preview_transfer(
                self._handwriting_source,
                self._handwriting_target,
                index,
                inspection,
                source_index_override=int(source_index),
            )
            return self._ok(
                index=index,
                page_count=inspection.page_count,
                before=_png_data_uri(before),
                after=_png_data_uri(after),
                ink=_png_data_uri(ink),
                stroke_count=stroke_count,
            )
        except Exception as exc:
            return self._error(exc)

    def choose_handwriting_source(self) -> dict:
        try:
            if self._window is None:
                raise PdfComposerError("앱 창이 아직 준비되지 않았습니다.")
            import webview

            selected = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=False,
                file_types=("필기 문서 (*.sdocx;*.notewise)",),
            )
            path = self._dialog_path(selected)
            if path is None:
                return self._ok(cancelled=True, **self._handwriting_status())
            self._handwriting_source = path
            return self._ok(cancelled=False, **self._handwriting_status())
        except Exception as exc:
            return self._error(exc)

    def choose_handwriting_target(self) -> dict:
        try:
            if self._window is None:
                raise PdfComposerError("앱 창이 아직 준비되지 않았습니다.")
            import webview

            selected = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=False,
                file_types=("PDF 문서 (*.pdf)",),
            )
            path = self._dialog_path(selected)
            if path is None:
                return self._ok(cancelled=True, **self._handwriting_status())
            self._handwriting_target = path
            return self._ok(cancelled=False, **self._handwriting_status())
        except Exception as exc:
            return self._error(exc)

    def reset_handwriting_transfer(self) -> dict:
        self._handwriting_source = None
        self._handwriting_target = None
        self._handwriting_cache = None
        return self._ok(**self._handwriting_status())

    def save_handwriting_transfer(
        self,
        suggested_name: str = "필기-이전.sdocx",
        target_mapping: list[int | None] | None = None,
    ) -> dict:
        try:
            if self._window is None:
                raise PdfComposerError("앱 창이 아직 준비되지 않았습니다.")
            inspection = self._inspection()
            import webview

            safe_name = re.sub(r'[<>:"/\\|?*]+', "_", suggested_name).strip(" .")
            suffix = output_suffix(self._handwriting_source)
            selected = self._window.create_file_dialog(
                webview.FileDialog.SAVE,
                save_filename=with_output_suffix(safe_name, self._handwriting_source)
                if safe_name else f"필기-이전{suffix}",
                file_types=(("Notewise 문서 (*.notewise)",) if suffix == ".notewise"
                            else ("Samsung Notes 문서 (*.sdocx)",)),
            )
            output = self._dialog_path(selected)
            if output is None:
                return self._ok(cancelled=True, inspection=inspection.as_dict())
            if target_mapping is not None and getattr(inspection, "mode", None) == "rebuild":
                from .page_match import match_from_target_mapping

                match = match_from_target_mapping(
                    inspection.source_page_count,
                    target_mapping,
                    inspection.match,
                )
                result = transfer_handwriting(
                    self._handwriting_source,
                    self._handwriting_target,
                    output,
                    match_override=match,
                )
            else:
                result = transfer_handwriting(
                    self._handwriting_source, self._handwriting_target, output
                )
            return self._ok(cancelled=False, result=result)
        except Exception as exc:
            return self._error(exc)

    def add_paths(self, paths: list[str]) -> dict:
        try:
            added = self._session.add_files(paths)
            return self._ok(
                added=added,
                sources=[source.as_dict() for source in self._session.sources],
            )
        except Exception as exc:
            return self._error(exc)

    def reset_documents(self) -> dict:
        """문서 합치기 쪽만 비운다.

        도구별로 따로 비울 수 있어야 한다. 한쪽을 정리하려다 다른 쪽에서 고르던 파일까지
        사라지면, 사용자는 하지도 않은 일을 당한다. 그래서 필기 옮기기 상태는 건드리지 않는다.
        """
        try:
            cleared = self._session.clear_sources()
            return self._ok(sources=[], cleared=[str(path) for path in cleared])
        except Exception as exc:
            return self._error(exc)

    def remove_document(self, document_id: str) -> dict:
        try:
            self._session.remove_source(document_id)
            return self._ok()
        except Exception as exc:
            return self._error(exc)

    def page_image(self, document_id: str, page_index: int, kind: str) -> dict:
        try:
            image = self._session.page_image(document_id, int(page_index), kind)
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
            if self._window is None:
                raise PdfComposerError("앱 창이 아직 준비되지 않았습니다.")
            import webview

            safe_name = re.sub(r'[<>:"/\\|?*]+', "_", suggested_name).strip(" .")
            if not safe_name.lower().endswith(".pdf"):
                safe_name += ".pdf"
            paths = self._window.create_file_dialog(
                webview.FileDialog.SAVE,
                save_filename=safe_name or "조합된 문서.pdf",
                file_types=("PDF 문서 (*.pdf)",),
            )
            if not paths:
                return self._ok(cancelled=True)
            output_path = paths if isinstance(paths, str) else paths[0]
            result = self._session.build_pdf(order, output_path)
            return self._ok(cancelled=False, result=result)
        except Exception as exc:
            return self._error(exc)

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._session.close()


def run(debug: bool = False) -> None:
    configure_windows_app_identity()
    import webview

    api = ComposerApi()
    static_file = Path(__file__).with_name("static") / "index.html"
    window = webview.create_window(
        "NotEditor",
        str(static_file.resolve()) + "#desktop",
        js_api=api,
        width=1440,
        height=900,
        min_size=(1080, 680),
        maximized=True,
        background_color="#0b1020",
        text_select=True,
    )
    api._bind_window(window)
    window.events.closed += api._close
    icon = Path(__file__).parents[1] / "assets" / "icon.ico"
    webview.start(
        debug=debug,
        http_server=True,
        private_mode=True,
        gui="edgechromium",
        icon=str(icon) if icon.exists() else None,
    )


def configure_logging() -> Path:
    import os

    local_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    log_dir = local_data / "NotEditor"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "app.log"
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
        force=True,
    )
    logging.getLogger("noteditor").info("Application starting (version %s)", __version__)
    return log_path
