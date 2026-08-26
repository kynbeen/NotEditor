"""Headless-ish WebView2 bridge diagnostic used by maintainers."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import webview

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from noteditor.app import ComposerApi


def main() -> None:
    page = ROOT / "noteditor" / "static" / "index.html"
    api = ComposerApi()
    window = webview.create_window(
        "PDF Page Composer bridge smoke test",
        page.resolve().as_uri(),
        js_api=api,
        hidden=True,
    )
    api._bind_window(window)
    window.events.closed += api._close

    def probe() -> None:
        time.sleep(1)
        raw = window.evaluate_js(
            "JSON.stringify({"
            "bridge: typeof window.pywebview,"
            "api: typeof window.pywebview?.api,"
            "health: typeof window.pywebview?.api?.health,"
            "addEnabled: !document.querySelector('#addPdfButton').disabled"
            "})"
        )
        result = json.loads(raw)
        print(json.dumps(result, ensure_ascii=False))
        if result != {
            "bridge": "object",
            "api": "object",
            "health": "function",
            "addEnabled": True,
        }:
            raise SystemExit(1)
        window.destroy()

    webview.start(probe, gui="edgechromium", private_mode=True)


if __name__ == "__main__":
    main()
