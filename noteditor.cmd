@echo off
rem ASCII only, CRLF only. cmd.exe misparses LF-only batch files and mangles
rem non-ASCII comments in the OEM codepage. Korean notes live in README.md and
rem in summary.ai pipeline/noteditor_link.py (_path_root).
rem
rem Put this folder on PATH and `noteditor` launches the desktop app from anywhere.
rem This file must stay in the NotEditor root: summary.ai locates the install with
rem shutil.which("noteditor") and treats this file's folder as the root.
setlocal
set "NE_ROOT=%~dp0"
if exist "%NE_ROOT%venv\Scripts\pythonw.exe" (
    start "" /d "%NE_ROOT%" "%NE_ROOT%venv\Scripts\pythonw.exe" -m noteditor %*
    exit /b 0
)
if exist "%NE_ROOT%dist\NotEditor\NotEditor.exe" (
    start "" /d "%NE_ROOT%" "%NE_ROOT%dist\NotEditor\NotEditor.exe" %*
    exit /b 0
)
echo NotEditor python environment not found. Run setup.ps1 first.
exit /b 1
