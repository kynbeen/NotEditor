# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_submodules

webview_data, webview_binaries, webview_hidden = collect_all("webview")

a = Analysis(
    ["launch.pyw"],
    pathex=[],
    binaries=webview_binaries,
    datas=webview_data + [
        ("noteditor/static", "noteditor/static"),
        ("assets/icon.ico", "assets"),
    ],
    hiddenimports=webview_hidden + collect_submodules("pymupdf") + ["pikepdf"],
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
