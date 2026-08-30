"""Session-isolated HTTP adapter for running NotEditor in a web browser."""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from . import __version__
from .app import MISSING_HANDWRITING_MESSAGE, ComposerApi
from .handwriting_transfer import (
    SUPPORTED_SUFFIXES,
    output_suffix,
    transfer_handwriting,
    with_output_suffix,
)
from .page_match import match_from_target_mapping

SESSION_COOKIE = "noteditor_session"
# Uptime pings arrive without a cookie, so giving them a session would mint a new
# ComposerApi and temp dir every few minutes for nobody to use.
SESSIONLESS_PATHS = frozenset({"/api/health"})
# 첫 화면만 세션을 심는다. 브라우저는 이 문서와 함께 정적 자산을 병렬로 요청하는데, 그것들이
# 저마다 세션을 만들면 접속 한 번에 빈 작업공간이 여러 개 생겨 TTL 동안 디스크에 남는다.
SESSION_ENTRY_PATHS = frozenset({"/", "/index.html"})
SESSION_TTL_SECONDS = int(os.environ.get("NOTEDITOR_SESSION_TTL", "7200"))
MAX_UPLOAD_BYTES = int(os.environ.get("NOTEDITOR_MAX_UPLOAD_MB", "512")) * 1024 * 1024
MAX_SESSIONS = int(os.environ.get("NOTEDITOR_MAX_SESSIONS", "200"))
SWEEP_INTERVAL_SECONDS = int(os.environ.get("NOTEDITOR_SWEEP_INTERVAL", "60"))


def needs_session(path: str) -> bool:
    """이 경로가 사용자별 작업공간을 필요로 하는가."""
    if path in SESSIONLESS_PATHS:
        return False
    return path in SESSION_ENTRY_PATHS or path.startswith("/api/")


@dataclass
class WebSession:
    api: ComposerApi
    touched_at: float

    def close(self) -> None:
        self.api._close()


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, WebSession] = {}
        self._lock = threading.RLock()

    def acquire(self, token: str | None) -> tuple[str, WebSession, bool]:
        now = time.monotonic()
        with self._lock:
            self._expire(now)
            if token and token in self._sessions:
                session = self._sessions[token]
                session.touched_at = now
                return token, session, False
            self._enforce_capacity()
            token = secrets.token_urlsafe(32)
            session = WebSession(ComposerApi(), now)
            self._sessions[token] = session
            return token, session, True

    def drop(self, token: str | None) -> None:
        """세션 하나를 통째로 닫는다. 임시 폴더도 함께 사라진다."""
        if not token:
            return
        with self._lock:
            session = self._sessions.pop(token, None)
        if session:
            session.close()

    def expire_idle(self) -> None:
        with self._lock:
            self._expire(time.monotonic())

    def _expire(self, now: float) -> None:
        expired = [
            token for token, session in self._sessions.items()
            if now - session.touched_at > SESSION_TTL_SECONDS
        ]
        for token in expired:
            self._sessions.pop(token).close()

    def _enforce_capacity(self) -> None:
        """세션 수에 상한을 둔다. 없으면 디스크가 먼저 차고 아무도 저장하지 못한다."""
        if MAX_SESSIONS <= 0 or len(self._sessions) < MAX_SESSIONS:
            return
        overflow = len(self._sessions) - MAX_SESSIONS + 1
        oldest = sorted(self._sessions.items(), key=lambda item: item[1].touched_at)
        for token, session in oldest[:overflow]:
            self._sessions.pop(token, None)
            session.close()
        logging.getLogger("noteditor.web").warning(
            "세션 상한 %d에 도달해 오래된 작업공간 %d개를 정리했습니다.", MAX_SESSIONS, overflow
        )

    def close(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close()


class RangeRequest(BaseModel):
    value: str
    page_count: int


class ExportRequest(BaseModel):
    order: list[dict]
    suggested_name: str = "조합된 문서.pdf"


class HandwritingExportRequest(BaseModel):
    suggested_name: str = "필기-이전.sdocx"
    target_mapping: list[int | None] | None = None


class ClientErrorRequest(BaseModel):
    message: str


store = SessionStore()
app = FastAPI(title="NotEditor", version=__version__, docs_url=None, redoc_url=None)


@app.middleware("http")
async def attach_session(request: Request, call_next):
    if not needs_session(request.url.path):
        # 상시 가동 핑과 정적 자산은 작업공간을 만들지 않는다. 핑은 정리만 굴린다.
        if request.url.path in SESSIONLESS_PATHS:
            store.expire_idle()
        return await call_next(request)
    token, session, created = store.acquire(request.cookies.get(SESSION_COOKIE))
    request.state.noteditor = session
    response = await call_next(request)
    # 이 응답은 특정 사용자의 것이다. 중간 프록시나 브라우저가 이걸 저장해 두면 다음 사람에게
    # 남의 문서가, 심지어 Set-Cookie가 실린 응답이면 남의 세션 자체가 건네진다.
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Vary"] = "Cookie"
    if created:
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            max_age=SESSION_TTL_SECONDS,
        )
    return response


@app.on_event("startup")
async def start_sweeper() -> None:
    """요청이 없어도 버려진 작업공간이 사라지도록 주기적으로 정리한다."""
    import asyncio

    async def sweep() -> None:
        while True:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            await run_in_threadpool(store.expire_idle)

    app.state.sweeper = asyncio.create_task(sweep())


@app.on_event("shutdown")
async def close_sessions() -> None:
    sweeper = getattr(app.state, "sweeper", None)
    if sweeper is not None:
        sweeper.cancel()
    store.close()


def _api(request: Request) -> ComposerApi:
    return request.state.noteditor.api


def _json_result(payload: dict) -> JSONResponse:
    return JSONResponse(payload, status_code=200 if payload.get("ok") else 400)


def _sanitize_name(value: str, fallback: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", Path(value).name).strip(" .") or fallback


def _safe_name(value: str, fallback: str, suffix: str) -> str:
    name = _sanitize_name(value, fallback)
    return name if name.lower().endswith(suffix) else name + suffix


def _upload_area(api: ComposerApi, area: str) -> Path:
    """도구마다 자기 업로드 폴더를 쓴다. 한 도구를 비울 때 남의 파일을 건드리지 않으려면
    애초에 섞어 두지 말아야 한다."""
    return api._session.temp_dir / "uploads" / area


def _purge_upload_area(api: ComposerApi, area: str) -> None:
    shutil.rmtree(_upload_area(api, area), ignore_errors=True)


async def _save_upload(api: ComposerApi, upload: UploadFile, suffix: str, area: str) -> Path:
    original = Path(upload.filename or "upload").name
    if Path(original).suffix.lower() != suffix:
        raise HTTPException(415, detail=f"{suffix} 파일만 업로드할 수 있습니다.")
    upload_dir = _upload_area(api, area)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_dir = upload_dir / uuid.uuid4().hex
    file_dir.mkdir()
    target = file_dir / _safe_name(original, "upload", suffix)
    size = 0
    try:
        with target.open("wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        413,
                        detail=f"파일 하나는 최대 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB까지 업로드할 수 있습니다.",
                    )
                handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        file_dir.rmdir()
        raise
    finally:
        await upload.close()
    return target


def _remove_upload(api: ComposerApi, path: Path | None) -> None:
    if path is None:
        return
    try:
        path.resolve().relative_to(api._session.temp_dir.resolve())
    except ValueError:
        return
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()
    except OSError:
        pass


@app.get("/api/health")
async def health() -> dict:
    # 동기 엔드포인트는 Starlette 작업 스레드를 거친다. 미리보기·쪽 매칭이 그 풀을
    # 점유한 순간에도 Render의 짧은 헬스체크가 기다리지 않도록 이벤트 루프에서 바로 답한다.
    return {"ok": True, "version": __version__, "runtime": "web"}


@app.head("/api/health")
async def health_head() -> Response:
    return Response(status_code=200)


@app.post("/api/client-error")
def client_error(payload: ClientErrorRequest) -> dict:
    logging.getLogger("noteditor.web").error("UI error: %s", payload.message)
    return {"ok": True}


@app.post("/api/documents")
async def add_documents(request: Request, files: list[UploadFile] = File(...)):
    api = _api(request)
    paths = [await _save_upload(api, upload, ".pdf", "documents") for upload in files]
    result = await run_in_threadpool(api.add_paths, [str(path) for path in paths])
    # 읽을 수 없는 PDF 하나 때문에 전부 등록되지 않으면, 방금 받은 사본이 갈 곳을 잃는다.
    registered = {source.path.resolve() for source in api._session.sources}
    for path in paths:
        if path.resolve() not in registered:
            _remove_upload(api, path)
    return _json_result(result)


@app.post("/api/documents/reset")
async def reset_documents(request: Request):
    """문서 합치기 쪽만 비운다. 필기 옮기기에서 고르던 파일은 그대로 둔다."""
    api = _api(request)
    result = await run_in_threadpool(api.reset_documents)
    if result.get("ok"):
        _purge_upload_area(api, "documents")
    return _json_result(result)


@app.delete("/api/documents/{document_id}")
async def remove_document(request: Request, document_id: str):
    api = _api(request)
    # 목록에서 빼기 전에 경로를 알아 둬야 사본을 지울 수 있다.
    uploaded = api._session.source_path(document_id)
    result = await run_in_threadpool(api.remove_document, document_id)
    if result.get("ok"):
        _remove_upload(api, uploaded)
    return _json_result(result)




@app.get("/api/documents/{document_id}/pages/{page_index}")
async def page_image(request: Request, document_id: str, page_index: int, kind: str = "thumbnail"):
    return _json_result(
        await run_in_threadpool(_api(request).page_image, document_id, page_index, kind)
    )


@app.post("/api/ranges")
async def parse_range(request: Request, payload: RangeRequest):
    return _json_result(
        await run_in_threadpool(_api(request).parse_range, payload.value, payload.page_count)
    )


@app.post("/api/documents/export")
async def export_documents(request: Request, payload: ExportRequest):
    api = _api(request)
    filename = _safe_name(payload.suggested_name, "조합된 문서.pdf", ".pdf")
    output = api._session.temp_dir / f"export-{uuid.uuid4().hex}.pdf"
    try:
        result = await run_in_threadpool(api._session.build_pdf, payload.order, output)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    headers = {
        "X-NotEditor-Page-Count": str(result["page_count"]),
        "X-NotEditor-Warnings": quote(json.dumps(result.get("warnings", []), ensure_ascii=False)),
    }
    return FileResponse(
        output,
        media_type="application/pdf",
        filename=filename,
        headers=headers,
        background=BackgroundTask(output.unlink, missing_ok=True),
    )


async def _set_handwriting_upload(request: Request, upload: UploadFile, kind: str):
    api = _api(request)
    if kind == "source":
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            return JSONResponse(
                {"ok": False, "error": ".sdocx 또는 .notewise 파일을 선택하세요."},
                status_code=400,
            )
    else:
        suffix = ".pdf"
    path = await _save_upload(api, upload, suffix, "handwriting")
    attribute = "_handwriting_source" if kind == "source" else "_handwriting_target"
    previous = getattr(api, attribute)
    setattr(api, attribute, path)
    api._handwriting_cache = None
    _remove_upload(api, previous)
    result = await run_in_threadpool(api._handwriting_status)
    return _json_result({"ok": True, "cancelled": False, **result})


@app.post("/api/handwriting/source")
async def upload_handwriting_source(request: Request, file: UploadFile = File(...)):
    return await _set_handwriting_upload(request, file, "source")


@app.post("/api/handwriting/target")
async def upload_handwriting_target(request: Request, file: UploadFile = File(...)):
    return await _set_handwriting_upload(request, file, "target")


@app.get("/api/handwriting/preview")
async def handwriting_preview(request: Request, page_index: int = 0, source_index: int = -2):
    return _json_result(
        await run_in_threadpool(_api(request).handwriting_preview, page_index, source_index)
    )


@app.post("/api/handwriting/reset")
async def reset_handwriting(request: Request):
    api = _api(request)
    result = api.reset_handwriting_transfer()
    if result.get("ok"):
        # 필기 폴더만 비운다. 문서 합치기에 올려 둔 PDF는 그대로 남는다.
        _purge_upload_area(api, "handwriting")
    return _json_result(result)


def _export_handwriting(api: ComposerApi, payload: HandwritingExportRequest, output: Path) -> dict:
    inspection = api._inspection()
    if payload.target_mapping is not None and inspection.mode == "rebuild":
        match = match_from_target_mapping(
            inspection.source_page_count,
            payload.target_mapping,
            inspection.match,
        )
        return transfer_handwriting(
            api._handwriting_source,
            api._handwriting_target,
            output,
            match_override=match,
        )
    return transfer_handwriting(api._handwriting_source, api._handwriting_target, output)


@app.post("/api/handwriting/export")
async def export_handwriting(request: Request, payload: HandwritingExportRequest):
    api = _api(request)
    if api._handwriting_source is None or api._handwriting_target is None:
        # 확장자를 읽기 전에 걸러야 한다. 아래 계산이 먼저 터지면 500이 나가고,
        # 사용자는 무엇을 안 골랐는지 알 수 없게 된다.
        return JSONResponse(
            {"ok": False, "error": MISSING_HANDWRITING_MESSAGE}, status_code=400
        )
    source = api._handwriting_source
    suffix = output_suffix(source)
    # 화면이 보낸 이름에 다른 필기 확장자가 붙어 와도 갈아 끼운다. 그대로 뒤에 붙이면
    # `문서-필기.sdocx.notewise` 처럼 두 형식이 섞인 이름이 내려간다.
    filename = with_output_suffix(_sanitize_name(payload.suggested_name, "필기-이전"), source)
    output = api._session.temp_dir / f"export-{uuid.uuid4().hex}{suffix}"
    try:
        result = await run_in_threadpool(_export_handwriting, api, payload, output)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return FileResponse(
        output,
        media_type="application/octet-stream",
        filename=filename,
        headers={"X-NotEditor-Page-Count": str(result.get("page_count", 0))},
        background=BackgroundTask(output.unlink, missing_ok=True),
    )


STATIC_DIR = Path(__file__).with_name("static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "noteditor.web:app",
        host=os.environ.get("NOTEDITOR_HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
