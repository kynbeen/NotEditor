# NotEditor 원터치 설치 스크립트
#
# GitHub 릴리스에서 최신 설치 파일을 내려받아 그대로 실행한다. 하는 일은 이 세 가지가
# 전부이고, 그 밖에 아무것도 보내거나 바꾸지 않는다.
#
#   1. 최신 릴리스 정보 조회 (api.github.com)
#   2. NotEditor-Setup-<버전>.exe 내려받기
#   3. 내려받은 설치 파일 실행
#
# 사용법:
#   powershell -ExecutionPolicy Bypass -File .\install-online.ps1
#
# 옵션:
#   -Silent   설치 마법사를 띄우지 않고 조용히 설치한다 (기본값)
#   -Wizard   설치 마법사를 띄워 설치 위치 등을 직접 고른다

[CmdletBinding()]
param(
    [switch]$Wizard,
    [string]$Repository = "kynbeen/NotEditor"
)

$ErrorActionPreference = "Stop"

# Windows PowerShell 5.1 은 TLS 1.2 를 기본으로 켜지 않아서 GitHub 연결이 끊긴다.
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch {
    # PowerShell 7 이상은 이미 TLS 1.2 이상을 쓴다.
}

function Write-Step {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Cyan
}

Write-Host ""
Write-Host "  NotEditor 설치" -ForegroundColor White
Write-Host "  ----------------------------------------" -ForegroundColor DarkGray

Write-Step "[1/3] 최신 버전을 확인하는 중..."
$ReleaseUrl = "https://api.github.com/repos/$Repository/releases/latest"
try {
    $Release = Invoke-RestMethod -Uri $ReleaseUrl -Headers @{
        "Accept"     = "application/vnd.github+json"
        "User-Agent" = "NotEditor-Installer"
    }
} catch {
    throw "최신 버전 정보를 가져오지 못했습니다. 인터넷 연결을 확인한 뒤 다시 실행하세요. ($($_.Exception.Message))"
}

$Version = $Release.tag_name
$Asset = $Release.assets | Where-Object { $_.name -like "NotEditor-Setup-*.exe" } | Select-Object -First 1
if (-not $Asset) {
    throw "이 릴리스($Version)에는 Windows 설치 파일이 없습니다. https://github.com/$Repository/releases 에서 직접 확인하세요."
}
$SizeMb = [math]::Round($Asset.size / 1MB, 1)
Write-Host "  찾았습니다: $($Asset.name) ($SizeMb MB)" -ForegroundColor DarkGray

Write-Step "[2/3] 설치 파일을 내려받는 중..."
$Destination = Join-Path $env:TEMP $Asset.name
$Progress = $ProgressPreference
$ProgressPreference = "SilentlyContinue"   # 진행률 표시줄이 다운로드를 크게 느리게 만든다
try {
    Invoke-WebRequest -Uri $Asset.browser_download_url -OutFile $Destination -UseBasicParsing
} catch {
    throw "설치 파일을 내려받지 못했습니다. ($($_.Exception.Message))"
} finally {
    $ProgressPreference = $Progress
}

$Downloaded = Get-Item -LiteralPath $Destination
if ($Downloaded.Length -ne $Asset.size) {
    Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
    throw "내려받은 파일 크기가 릴리스와 다릅니다. 네트워크 문제일 수 있으니 다시 실행하세요."
}
# 인터넷에서 받은 표시를 지워 SmartScreen 경고 없이 실행되게 한다. 방금 이 스크립트가
# GitHub 릴리스에서 직접 받아 크기까지 확인한 파일이다.
Unblock-File -LiteralPath $Destination -ErrorAction SilentlyContinue

Write-Step "[3/3] 설치하는 중... (관리자 권한을 묻는 창이 뜨면 '예'를 누르세요)"
$Arguments = if ($Wizard) { @() } else { @("/SILENT", "/SP-", "/NORESTART") }
$Process = Start-Process -FilePath $Destination -ArgumentList $Arguments -Wait -PassThru
if ($Process.ExitCode -ne 0) {
    throw "설치 프로그램이 오류로 끝났습니다 (코드 $($Process.ExitCode)). 내려받은 파일을 직접 실행해 보세요: $Destination"
}

Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "  설치가 끝났습니다. ($Version)" -ForegroundColor Green
Write-Host "  시작 메뉴 또는 바탕화면에서 'NotEditor' 를 실행하세요." -ForegroundColor Green
Write-Host ""
