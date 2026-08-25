"""Headless-ish WebView2 bridge diagnostic used by maintainers."""

from __future__ import annotations

import json
import time
from pathlib import Path

import webview


class ProbeApi:
    def ping(self) -> str:
        return "pong"

    def health(self) -> dict:
        return {"ok": True, "version": "smoke"}


def main() -> None:
    page = Path(__file__).parents[1] / "pdf_page_composer" / "static" / "index.html"
    window = webview.create_window(
        "PDF Page Composer bridge smoke test",
        page.resolve().as_uri(),
        js_api=ProbeApi(),
        hidden=True,
    )

    def probe() -> None:
        time.sleep(1)
        raw = window.evaluate_js(
            "JSON.stringify({"
            "bridge: typeof window.pywebview,"
            "api: typeof window.pywebview?.api,"
            "ping: typeof window.pywebview?.api?.ping,"
            "addEnabled: !document.querySelector('#addPdfButton').disabled"
            "})"
        )
        result = json.loads(raw)
        print(json.dumps(result, ensure_ascii=False))
        if result != {
            "bridge": "object",
            "api": "object",
            "ping": "function",
            "addEnabled": True,
        }:
            raise SystemExit(1)
        window.destroy()

    webview.start(probe, gui="edgechromium", private_mode=True)


if __name__ == "__main__":
    main()
