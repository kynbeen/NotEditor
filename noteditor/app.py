from __future__ import annotations

import logging
import os
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

# pikepdf의 nanobind 열거형 등록은 첫 import가 두 스레드에서 겹치면 프로세스를 종료할 수 있다.
# 분석 작업 스레드를 만들기 전에 주 스레드에서 한 번 초기화한다.
import pikepdf  # noqa: F401

from . import __version__
from .engine import ComposerSession, PdfComposerError
from .merge_handoff import (
    CONTRACT_VERSION,
    MergePlan,
    load_merge_plan,
    parts_from_order,
    paths_refer_to_same_file,
    sidecar_path,
    write_sidecar,
)
from .page_plan import PagePlan
from .ranges import PageRangeError, parse_page_ranges
from .handwriting_transfer import (
    SUPPORTED_SUFFIXES,
    inspect_transfer,
    output_suffix,
    preview_transfer,
    transfer_handwriting,
    with_output_suffix,
)

APP_USER_MODEL_ID = "NotEditor.Desktop"
MISSING_HANDWRITING_MESSAGE = "필기 원본과 대상 PDF를 모두 선택하세요."
HANDWRITING_ANALYSIS_CONCURRENCY = max(
    1, int(os.environ.get("NOTEDITOR_ANALYSIS_CONCURRENCY", "1"))
)
_HANDWRITING_ANALYSIS_EXECUTOR = ThreadPoolExecutor(
    max_workers=HANDWRITING_ANALYSIS_CONCURRENCY,
    thread_name_prefix="noteditor-analysis",
)
_ANALYSIS_MESSAGES = {
    "waiting": "두 파일을 선택해 주세요.",
    "structure": "파일 구조 확인 중…",
    "matching": "페이지 비교·자동 매칭 중…",
    "alignment": "필기 좌표 정렬 중…",
    "preview": "미리보기 준비 중…",
    "ready": "분석이 끝났습니다.",
    "error": "분석하지 못했습니다.",
}


def _png_data_uri(payload: bytes) -> str:
    import base64

    return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")


def configure_windows_app_identity(app_id: str = APP_USER_MODEL_ID) -> None:
    """Python 프로세스가 아니라 독립 앱으로 작업표시줄에 그룹화되게 한다."""
    import os

    if os.name != "nt":
        return
    try:
        import ctypes

        set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        result = set_app_id(app_id)
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

    def __init__(
        self,
        session: ComposerSession | None = None,
        startup_plan_path: str | Path | None = None,
        preloaded_startup_plan: MergePlan | None = None,
    ) -> None:
        # pywebview exposes every public attribute on js_api. Keep native and
        # stateful Python objects private or its serializer walks the complete
        # WinForms/WebView2 object graph and eventually recurses forever.
        self._session = session or ComposerSession()
        self._startup_plan_path = (
            Path(startup_plan_path).expanduser().resolve() if startup_plan_path else None
        )
        self._startup_plan = preloaded_startup_plan
        self._startup_plan_result: dict | None = None
        self._merge_output_path: Path | None = None
        self._window: Any | None = None
        self._closed = False
        self._handwriting_source: Path | None = None
        self._handwriting_target: Path | None = None
        self._handwriting_cache: tuple | None = None
        self._handwriting_lock = threading.RLock()
        self._handwriting_generation = 0
        self._handwriting_future: Future | None = None
        self._handwriting_analysis = {
            "state": "waiting",
            "stage": "waiting",
            "message": _ANALYSIS_MESSAGES["waiting"],
            "error": None,
        }

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

    def startup_plan(self) -> dict:
        """Load a summary.ai handoff once and map paths to this session's IDs."""
        try:
            if self._startup_plan_path is None:
                return self._ok(plan=None)
            if self._startup_plan_result is not None:
                return self._ok(plan=self._startup_plan_result)
            if self._session.sources:
                raise PdfComposerError("시작 계획은 빈 문서 작업공간에만 적용할 수 있습니다.")

            plan = self._startup_plan or load_merge_plan(self._startup_plan_path)
            unique_paths = list(dict.fromkeys(part.path for part in plan.parts))
            self._session.add_files(unique_paths)
            sources = self._session.sources
            source_by_path = {
                os.path.normcase(str(source.path.resolve())): source for source in sources
            }
            order: list[dict] = []
            for part in plan.parts:
                source = source_by_path.get(os.path.normcase(str(part.path.resolve())))
                if source is None:
                    raise PdfComposerError(f"시작 계획의 PDF를 등록하지 못했습니다: {part.path.name}")
                indices = (
                    parse_page_ranges(part.pages, source.page_count)
                    if part.pages
                    else list(range(source.page_count))
                )
                order.extend(
                    {"document_id": source.id, "page_index": index} for index in indices
                )
            if len({(item["document_id"], item["page_index"]) for item in order}) != len(order):
                raise PdfComposerError("합치기 계획에 같은 페이지가 두 번 들어 있습니다.")
            # Contract v1 cannot describe arbitrary interleaving or reverse order.
            parts_from_order(order, sources)

            result = {
                "version": CONTRACT_VERSION,
                "title": plan.title,
                "output_path": str(plan.output_path),
                "output_name": plan.output_path.name,
                "sources": [source.as_dict() for source in sources],
                "order": order,
            }
            self._startup_plan = plan
            self._merge_output_path = plan.output_path
            self._startup_plan_result = result
            return self._ok(plan=result)
        except Exception as exc:
            if self._startup_plan_result is None and self._session.sources:
                self._session.clear_sources()
            return self._error(exc)

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
        with self._handwriting_lock:
            source = self._handwriting_source
            target = self._handwriting_target
            if not source or not target:
                raise PdfComposerError(MISSING_HANDWRITING_MESSAGE)
            key = self._handwriting_key(source, target)
            cached = self._handwriting_cache
            if cached and cached[0] == key:
                return cached[1]
            analysis = dict(self._handwriting_analysis)
            future = self._handwriting_future
        if future is not None and not future.done():
            raise PdfComposerError("필기 문서를 분석하는 중입니다. 잠시 후 다시 시도하세요.")
        if analysis["state"] == "error":
            raise PdfComposerError(analysis["error"] or _ANALYSIS_MESSAGES["error"])

        # 테스트나 데스크톱 내부 호출처럼 선택 도우미를 거치지 않은 경우만 동기로 계산한다.
        # 정상 UI 경로는 _start_handwriting_analysis가 백그라운드에서 이 캐시를 채운다.
        inspection = inspect_transfer(source, target)
        with self._handwriting_lock:
            if source == self._handwriting_source and target == self._handwriting_target:
                self._handwriting_cache = (key, inspection)
        return inspection

    @staticmethod
    def _handwriting_key(source: Path, target: Path) -> tuple:
        return (
            str(source),
            str(target),
            source.stat().st_mtime_ns,
            target.stat().st_mtime_ns,
        )

    def _set_analysis_stage(self, generation: int, stage: str) -> None:
        with self._handwriting_lock:
            if generation != self._handwriting_generation:
                return
            self._handwriting_analysis = {
                "state": "running",
                "stage": stage,
                "message": _ANALYSIS_MESSAGES[stage],
                "error": None,
            }

    def _run_handwriting_analysis(
        self, generation: int, source: Path, target: Path, inspector
    ) -> None:
        try:
            inspection = inspector(
                source,
                target,
                progress=lambda stage: self._set_analysis_stage(generation, stage),
            )
            key = self._handwriting_key(source, target)
        except Exception as exc:
            with self._handwriting_lock:
                if generation != self._handwriting_generation:
                    return
                self._handwriting_cache = None
                self._handwriting_analysis = {
                    "state": "error",
                    "stage": "error",
                    "message": _ANALYSIS_MESSAGES["error"],
                    "error": str(exc),
                }
            return
        with self._handwriting_lock:
            if generation != self._handwriting_generation:
                return
            self._handwriting_cache = (key, inspection)
            self._handwriting_analysis = {
                "state": "ready",
                "stage": "ready",
                "message": _ANALYSIS_MESSAGES["ready"],
                "error": None,
            }

    def _start_handwriting_analysis(self) -> None:
        with self._handwriting_lock:
            self._handwriting_generation += 1
            generation = self._handwriting_generation
            previous = self._handwriting_future
            self._handwriting_future = None
            self._handwriting_cache = None
            source = self._handwriting_source
            target = self._handwriting_target
            if previous is not None:
                previous.cancel()
            if not source or not target:
                self._handwriting_analysis = {
                    "state": "waiting",
                    "stage": "waiting",
                    "message": _ANALYSIS_MESSAGES["waiting"],
                    "error": None,
                }
                return
            self._handwriting_analysis = {
                "state": "running",
                "stage": "structure",
                "message": _ANALYSIS_MESSAGES["structure"],
                "error": None,
            }
            self._handwriting_future = _HANDWRITING_ANALYSIS_EXECUTOR.submit(
                self._run_handwriting_analysis, generation, source, target, inspect_transfer
            )

    def _set_handwriting_path(self, kind: str, path: Path) -> Path | None:
        path = path.expanduser().resolve()
        if not path.is_file():
            raise PdfComposerError(f"파일을 찾을 수 없습니다: {path.name}")
        if kind == "source":
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                raise PdfComposerError(".sdocx 또는 .notewise 파일을 선택하세요.")
            attribute = "_handwriting_source"
        elif kind == "target":
            if path.suffix.lower() != ".pdf":
                raise PdfComposerError(".pdf 파일을 선택하세요.")
            attribute = "_handwriting_target"
        else:
            raise PdfComposerError("알 수 없는 필기 파일 종류입니다.")
        with self._handwriting_lock:
            previous = getattr(self, attribute)
            setattr(self, attribute, path)
        self._start_handwriting_analysis()
        return previous

    def _handwriting_status(self) -> dict:
        with self._handwriting_lock:
            source = self._handwriting_source
            target = self._handwriting_target
            analysis = dict(self._handwriting_analysis)
            cached = self._handwriting_cache
        inspection = cached[1].as_dict() if cached and analysis["state"] == "ready" else None
        return {
            "source_name": source.name if source else None,
            "source_format": source.suffix.lower().lstrip(".") if source else None,
            "target_name": target.name if target else None,
            "ready": inspection is not None,
            "inspection": inspection,
            "analysis": analysis,
        }

    def handwriting_status(self) -> dict:
        return self._ok(**self._handwriting_status())

    def retry_handwriting_analysis(self) -> dict:
        try:
            if not self._handwriting_source or not self._handwriting_target:
                raise PdfComposerError(MISSING_HANDWRITING_MESSAGE)
            self._start_handwriting_analysis()
            return self._ok(**self._handwriting_status())
        except Exception as exc:
            return self._error(exc)

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
            self._set_handwriting_path("source", path)
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
            self._set_handwriting_path("target", path)
            return self._ok(cancelled=False, **self._handwriting_status())
        except Exception as exc:
            return self._error(exc)

    def reset_handwriting_transfer(self) -> dict:
        with self._handwriting_lock:
            self._handwriting_source = None
            self._handwriting_target = None
        self._start_handwriting_analysis()
        return self._ok(**self._handwriting_status())

    def save_handwriting_transfer(
        self,
        suggested_name: str = "필기-이전.sdocx",
        page_plan: list[dict] | list[int | None] | None = None,
        allow_unconfirmed: bool = False,
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
            if page_plan is not None and all(isinstance(item, dict) for item in page_plan):
                plan = PagePlan.from_payload(
                    inspection.source_page_count,
                    inspection.page_count,
                    page_plan,
                    inspection.match,
                )
                if plan.unconfirmed and not allow_unconfirmed:
                    raise PdfComposerError(
                        f"확인하지 않은 쪽 대응이 {len(plan.unconfirmed)}개 남아 있습니다."
                    )
                result = transfer_handwriting(
                    self._handwriting_source,
                    self._handwriting_target,
                    output,
                    plan_override=plan,
                )
                if plan.unconfirmed:
                    result.setdefault("warnings", []).append(
                        f"확인하지 않은 쪽 대응 {len(plan.unconfirmed)}개를 사용자 승인으로 저장했습니다: "
                        + ", ".join(plan.unconfirmed_labels)
                    )
            elif page_plan is not None and getattr(inspection, "mode", None) == "rebuild":
                from .page_match import match_from_target_mapping

                match = match_from_target_mapping(
                    inspection.source_page_count,
                    page_plan,
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
            if self._merge_output_path is not None:
                output_path = self._merge_output_path
                for source in self._session.sources:
                    if paths_refer_to_same_file(source.path, output_path):
                        raise PdfComposerError("합치기 결과가 원본 PDF를 덮어쓸 수 없습니다.")
                sidecar_parts = parts_from_order(order, self._session.sources)
                # A prior failed/retried save must never leave a stale completion
                # marker beside a newly written or failed PDF.
                sidecar_path(output_path).unlink(missing_ok=True)
                result = self._session.build_pdf(order, output_path)
                sidecar = write_sidecar(
                    result["path"],
                    parts=sidecar_parts,
                    noteditor_version=__version__,
                )
                result["sidecar"] = str(sidecar)
                return self._ok(cancelled=False, result=result)

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
        with self._handwriting_lock:
            self._handwriting_generation += 1
            if self._handwriting_future is not None:
                self._handwriting_future.cancel()
        self._session.close()


def run(debug: bool = False, open_plan: str | Path | None = None) -> None:
    configure_windows_app_identity()
    import webview

    startup = load_merge_plan(open_plan) if open_plan else None
    api = ComposerApi(
        startup_plan_path=open_plan,
        preloaded_startup_plan=startup,
    )
    static_file = Path(__file__).with_name("static") / "index.html"
    window_title = "NotEditor"
    if startup and startup.title:
        window_title += f" — {startup.title}"
    window = webview.create_window(
        window_title,
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
