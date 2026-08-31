"""Windows launcher for the loopback-only NotEditor web application."""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import subprocess
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from . import __version__
from .app import configure_windows_app_identity

LOCAL_WEB_HOST = "127.0.0.1"
DEFAULT_LOCAL_WEB_PORT = 8765
LOCAL_WEB_INSTANCE = "local-web"
LOCAL_WEB_APP_USER_MODEL_ID = "NotEditor.LocalWeb"
STARTUP_TIMEOUT_SECONDS = 15.0


class LocalWebLauncherError(RuntimeError):
    """The local web launcher cannot safely start or reuse its server."""


def local_web_url(port: int = DEFAULT_LOCAL_WEB_PORT) -> str:
    return f"http://{LOCAL_WEB_HOST}:{port}/"


def user_data_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "NotEditor"


def configure_local_web_logging() -> Path:
    root = user_data_root()
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / "local-web.log"
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
        force=True,
    )
    logging.getLogger("noteditor.local_web").info(
        "Local web launcher starting (version %s)", __version__
    )
    return log_path


def _registry_browser_paths() -> Iterable[Path]:
    if os.name != "nt":
        return ()
    try:
        import winreg
    except ImportError:
        return ()

    paths: list[Path] = []
    key_name = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for executable in ("msedge.exe", "chrome.exe"):
            try:
                with winreg.OpenKey(hive, f"{key_name}\\{executable}") as key:
                    value, _ = winreg.QueryValueEx(key, None)
            except OSError:
                continue
            paths.append(Path(value))
    return tuple(paths)


def browser_candidates() -> tuple[Path, ...]:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    program_files = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    program_files_x86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    paths = [
        program_files_x86 / "Microsoft/Edge/Application/msedge.exe",
        program_files / "Microsoft/Edge/Application/msedge.exe",
        local / "Microsoft/Edge/Application/msedge.exe",
        program_files / "Google/Chrome/Application/chrome.exe",
        program_files_x86 / "Google/Chrome/Application/chrome.exe",
        local / "Google/Chrome/Application/chrome.exe",
        *_registry_browser_paths(),
    ]
    # Registry and conventional locations often point to the same executable.
    return tuple(dict.fromkeys(path for path in paths if str(path)))


def find_app_browser(candidates: Iterable[Path] | None = None) -> Path | None:
    for path in candidates if candidates is not None else browser_candidates():
        if path.is_file():
            return path.resolve()
    return None


def browser_command(browser: Path, url: str, profile: Path) -> list[str]:
    return [
        str(browser),
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-mode",
    ]


def launch_app_browser(url: str) -> subprocess.Popen:
    browser = find_app_browser()
    if browser is None:
        raise LocalWebLauncherError(
            "Microsoft Edge 또는 Google Chrome을 찾지 못했습니다. "
            "브라우저를 설치한 뒤 다시 실행하세요."
        )
    profile = user_data_root() / "LocalWebProfile"
    profile.mkdir(parents=True, exist_ok=True)
    logging.getLogger("noteditor.local_web").info(
        "Opening local web app with %s", browser
    )
    return subprocess.Popen(browser_command(browser, url, profile))


def probe_server(
    port: int,
    *,
    opener: Callable = urlopen,
    timeout: float = 0.8,
) -> dict | None:
    try:
        with opener(f"{local_web_url(port)}api/health", timeout=timeout) as response:
            if getattr(response, "status", 200) != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def is_local_noteditor(payload: dict | None) -> bool:
    return bool(
        payload
        and payload.get("ok") is True
        and payload.get("runtime") == "web"
        and payload.get("instance") == LOCAL_WEB_INSTANCE
    )


def port_is_available(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((LOCAL_WEB_HOST, port))
    except OSError:
        return False
    return True


def wait_until_ready(port: int, server, timeout: float = STARTUP_TIMEOUT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_local_noteditor(probe_server(port)):
            return
        if getattr(server, "should_exit", False):
            break
        time.sleep(0.05)
    raise LocalWebLauncherError(
        f"로컬 웹 서버가 {timeout:g}초 안에 준비되지 않았습니다."
    )


def _run_owned_server(port: int) -> None:
    # The marker lets a second shortcut distinguish this server from an unrelated
    # service or a manually started NotEditor development server on the same port.
    os.environ["NOTEDITOR_INSTANCE"] = LOCAL_WEB_INSTANCE

    import uvicorn

    from .web import app

    server = uvicorn.Server(uvicorn.Config(
        app,
        host=LOCAL_WEB_HOST,
        port=port,
        proxy_headers=False,
        server_header=False,
        access_log=False,
        log_config=None,
    ))
    errors: list[BaseException] = []

    def open_and_watch_browser() -> None:
        try:
            wait_until_ready(port, server)
            process = launch_app_browser(local_web_url(port))
            return_code = process.wait()
            logging.getLogger("noteditor.local_web").info(
                "App-mode browser exited with code %s", return_code
            )
        except BaseException as exc:  # propagate after the server has stopped
            errors.append(exc)
            logging.getLogger("noteditor.local_web").exception(
                "Failed while coordinating the local browser"
            )
        finally:
            server.should_exit = True

    watcher = threading.Thread(
        target=open_and_watch_browser,
        name="noteditor-local-browser",
        daemon=True,
    )
    watcher.start()
    server.run()
    watcher.join(timeout=2)
    if errors:
        raise LocalWebLauncherError(str(errors[0])) from errors[0]


def run_local_web(port: int = DEFAULT_LOCAL_WEB_PORT) -> str:
    if not 1 <= port <= 65535:
        raise LocalWebLauncherError(f"올바르지 않은 포트입니다: {port}")
    current = probe_server(port)
    if is_local_noteditor(current):
        # This process does not own the existing server, so it only opens another
        # window and exits. It must never stop somebody else's working session.
        launch_app_browser(local_web_url(port))
        return "reused"
    if current is not None or not port_is_available(port):
        raise LocalWebLauncherError(
            f"로컬 웹 전용 포트 {port}을 다른 프로그램이 사용 중입니다. "
            "그 프로그램을 종료한 뒤 다시 실행하세요."
        )
    _run_owned_server(port)
    return "owned"


def _show_startup_error(message: str, log_path: Path) -> None:
    try:
        from tkinter import messagebox

        messagebox.showerror(
            "NotEditor 로컬 웹",
            "로컬 웹을 시작하지 못했습니다.\n\n"
            f"오류: {message}\n\n진단 기록: {log_path}",
        )
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="NotEditor를 로컬 웹 앱으로 실행합니다.")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("NOTEDITOR_LOCAL_PORT", DEFAULT_LOCAL_WEB_PORT)),
        help="루프백 웹 서버 포트",
    )
    args = parser.parse_args()
    log_path = configure_local_web_logging()
    configure_windows_app_identity(LOCAL_WEB_APP_USER_MODEL_ID)
    try:
        run_local_web(args.port)
    except Exception as exc:
        logging.getLogger("noteditor.local_web").error(
            "Local web launcher failed\n%s", traceback.format_exc()
        )
        _show_startup_error(str(exc), log_path)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
