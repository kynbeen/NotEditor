# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

webview_data, webview_binaries, webview_hidden = collect_all("webview")

# 배포 빌드는 `python -m noteditor.stamp_version` 이 먼저 만들어 둔다. 이 파일이 빠지면
# 설치된 앱은 깃이 없어 자기 버전을 모른다. 없을 때 hiddenimports 에 넣으면 빌드가
# 경고를 내므로, 있을 때만 넣는다.
version_stamp = ["noteditor._version"] if Path("noteditor/_version.py").exists() else []

a = Analysis(
    ["launch.pyw"],
    pathex=[],
    binaries=webview_binaries,
    datas=webview_data + [
        ("noteditor/static", "noteditor/static"),
        ("assets/icon.ico", "assets"),
    ],
    hiddenimports=webview_hidden + collect_submodules("pymupdf") + ["pikepdf"] + version_stamp,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter.test"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NotEditor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon="assets/icon.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="NotEditor",
)
