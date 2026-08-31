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

Write-Host "[4/5] 시작 메뉴와 바탕화면에 바로가기를 만드는 중..." -ForegroundColor Cyan
$Shell = New-Object -ComObject WScript.Shell
$ShortcutDefinitions = @(
    @{
        Name = "NotEditor.lnk"
        Arguments = "-m noteditor"
        Description = "PDF 문서 합치기와 필기 옮기기 데스크톱 앱"
    },
    @{
        Name = "NotEditor 로컬 웹.lnk"
        Arguments = "-m noteditor.local_web"
        Description = "로컬 PC에서 실행되는 NotEditor 웹 앱"
    }
)
foreach ($Definition in $ShortcutDefinitions) {
    foreach ($Folder in @(
        [Environment]::GetFolderPath("Programs"),
        [Environment]::GetFolderPath("Desktop")
    )) {
        $ShortcutPath = Join-Path $Folder $Definition.Name
        $Shortcut = $Shell.CreateShortcut($ShortcutPath)
        $Shortcut.TargetPath = $PythonWindowPath
        $Shortcut.Arguments = $Definition.Arguments
        $Shortcut.WorkingDirectory = $InstallRoot
        $Shortcut.IconLocation = "$IconPath,0"
        $Shortcut.WindowStyle = 1
        $Shortcut.Description = $Definition.Description
        $Shortcut.Save()
    }
}

# --- 5. PATH 등록 ---------------------------------------------------------------
# 이 폴더를 사용자 PATH 에 넣으면 어디서나 `noteditor` 로 실행할 수 있고, summary.ai 처럼
# 연동하는 앱이 **바로가기를 파싱하지 않고** 설치 위치를 곧바로 찾을 수 있다.
# (바로가기는 COM 으로만 읽을 수 있어 확인할 때마다 PowerShell 프로세스가 하나씩 뜬다.)
Write-Host "[5/5] PATH 에 등록하는 중..." -ForegroundColor Cyan
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$Entries = @($UserPath -split ';' | Where-Object { $_ -ne '' })
$AlreadyThere = $Entries | Where-Object { $_.TrimEnd('\') -ieq $InstallRoot.TrimEnd('\') }
if ($AlreadyThere) {
    Write-Host "  이미 등록되어 있습니다: $InstallRoot" -ForegroundColor DarkGray
} else {
    $NewPath = (@($Entries) + $InstallRoot) -join ';'
    [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
    $env:Path = "$env:Path;$InstallRoot"
    Write-Host "  PATH 에 추가: $InstallRoot" -ForegroundColor DarkGray
    Write-Host "  이미 열려 있는 창에는 적용되지 않습니다. 새 터미널을 여세요." -ForegroundColor DarkGray
}

Write-Host "NotEditor 설치가 끝났습니다. 데스크톱 앱, 'NotEditor 로컬 웹', 또는 터미널에서 'noteditor' 로 실행하세요." -ForegroundColor Green
