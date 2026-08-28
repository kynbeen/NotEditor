"""Session-isolated HTTP adapter for running NotEditor in a web browser."""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
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
from .app import ComposerApi
from .page_match import match_from_target_mapping
from .sdocx_transfer import transfer_handwriting

SESSION_COOKIE = "noteditor_session"
SESSION_TTL_SECONDS = int(os.environ.get("NOTEDITOR_SESSION_TTL", "7200"))
MAX_UPLOAD_BYTES = int(os.environ.get("NOTEDITOR_MAX_UPLOAD_MB", "512")) * 1024 * 1024


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
            token = secrets.token_urlsafe(32)
            session = WebSession(ComposerApi(), now)
            self._sessions[token] = session
            return token, session, True

    def _expire(self, now: float) -> None:
        expired = [
            token for token, session in self._sessions.items()
            if now - session.touched_at > SESSION_TTL_SECONDS
        ]
        for token in expired:
            self._sessions.pop(token).close()

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
    token, session, created = store.acquire(request.cookies.get(SESSION_COOKIE))
    request.state.noteditor = session
    response = await call_next(request)
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


@app.on_event("shutdown")
def close_sessions() -> None:
    store.close()


def _api(request: Request) -> ComposerApi:
    return request.state.noteditor.api


def _json_result(payload: dict) -> JSONResponse:
    return JSONResponse(payload, status_code=200 if payload.get("ok") else 400)


def _safe_name(value: str, fallback: str, suffix: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]+', "_", Path(value).name).strip(" .") or fallback
    return name if name.lower().endswith(suffix) else name + suffix


async def _save_upload(api: ComposerApi, upload: UploadFile, suffix: str) -> Path:
    original = Path(upload.filename or "upload").name
    if Path(original).suffix.lower() != suffix:
        raise HTTPException(415, detail=f"{suffix} 파일만 업로드할 수 있습니다.")
    upload_dir = api._session.temp_dir / "uploads"
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
def health() -> dict:
    return {"ok": True, "version": __version__, "runtime": "web"}


@app.head("/api/health")
def health_head() -> Response:
    return Response(status_code=200)


@app.post("/api/client-error")
def client_error(payload: ClientErrorRequest) -> dict:
    logging.getLogger("noteditor.web").error("UI error: %s", payload.message)
    return {"ok": True}


@app.post("/api/documents")
async def add_documents(request: Request, files: list[UploadFile] = File(...)):
    api = _api(request)
    paths = [await _save_upload(api, upload, ".pdf") for upload in files]
    result = await run_in_threadpool(api.add_paths, [str(path) for path in paths])
    return _json_result(result)


@app.delete("/api/documents/{document_id}")
async def remove_document(request: Request, document_id: str):
    return _json_result(await run_in_threadpool(_api(request).remove_document, document_id))


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
    suffix = ".sdocx" if kind == "source" else ".pdf"
    path = await _save_upload(api, upload, suffix)
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
    source, target = api._handwriting_source, api._handwriting_target
    result = api.reset_handwriting_transfer()
    _remove_upload(api, source)
    _remove_upload(api, target)
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
    filename = _safe_name(payload.suggested_name, "필기-이전.sdocx", ".sdocx")
    output = api._session.temp_dir / f"export-{uuid.uuid4().hex}.sdocx"
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
