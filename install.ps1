$ErrorActionPreference = "Stop"

$InstallRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPath = Join-Path $InstallRoot "venv"
$PythonPath = Join-Path $VenvPath "Scripts\python.exe"
$PythonWindowPath = Join-Path $VenvPath "Scripts\pythonw.exe"
$IconPath = Join-Path $InstallRoot "assets\icon.ico"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.12 이상이 필요합니다. https://www.python.org/downloads/ 에서 설치한 뒤 다시 실행하세요."
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    Write-Host "[1/4] NotEditor 전용 Python 환경을 만드는 중..." -ForegroundColor Cyan
    python -m venv $VenvPath
}

Write-Host "[2/4] 필요한 구성 요소를 설치하는 중..." -ForegroundColor Cyan
& $PythonPath -m pip install --upgrade pip
& $PythonPath -m pip install -r (Join-Path $InstallRoot "requirements.txt")

Write-Host "[3/4] 앱 아이콘을 만드는 중..." -ForegroundColor Cyan
& $PythonPath -m noteditor.make_icon

Write-Host "[4/4] 시작 메뉴와 바탕화면에 바로가기를 만드는 중..." -ForegroundColor Cyan
$Shell = New-Object -ComObject WScript.Shell
$ShortcutTargets = @(
    (Join-Path ([Environment]::GetFolderPath("Programs")) "NotEditor.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "NotEditor.lnk")
)
foreach ($ShortcutPath in $ShortcutTargets) {
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $PythonWindowPath
    $Shortcut.Arguments = "-m noteditor"
    $Shortcut.WorkingDirectory = $InstallRoot
    $Shortcut.IconLocation = "$IconPath,0"
    $Shortcut.WindowStyle = 1
    $Shortcut.Description = "PDF 문서 합치기와 Samsung Notes 필기 옮기기"
    $Shortcut.Save()
}

Write-Host "NotEditor 설치가 끝났습니다. 바탕화면의 NotEditor를 실행하세요." -ForegroundColor Green
