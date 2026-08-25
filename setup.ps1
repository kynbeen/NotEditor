$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Root "venv"
$Python = Join-Path $Venv "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host "Python 가상환경 생성 중..." -ForegroundColor Cyan
    python -m venv $Venv
}

& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $Root "requirements.txt")
& $Python -m pdf_page_composer.make_icon

Write-Host "설치 완료. .\install-app.ps1 을 실행해 바로가기를 만드세요." -ForegroundColor Green
