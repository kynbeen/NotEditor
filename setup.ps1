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
& $Python -m noteditor.make_icon

Write-Host "개발 환경 준비 완료. 원터치 사용자 설치는 .\install.ps1 을 실행하세요." -ForegroundColor Green
