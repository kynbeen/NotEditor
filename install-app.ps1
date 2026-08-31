$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Pyw = Join-Path $Root "venv\Scripts\pythonw.exe"
$Icon = Join-Path $Root "assets\icon.ico"

if (-not (Test-Path -LiteralPath $Pyw)) {
    throw "먼저 setup.ps1을 실행하세요: $Pyw 없음"
}
# 코드와 함께 아이콘이 바뀔 수 있으므로 기존 파일이 있어도 매번 다시 만든다.
& (Join-Path $Root "venv\Scripts\python.exe") -m noteditor.make_icon

$shell = New-Object -ComObject WScript.Shell
$definitions = @(
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

foreach ($definition in $definitions) {
    foreach ($folder in @(
        [Environment]::GetFolderPath("Programs"),
        [Environment]::GetFolderPath("Desktop")
    )) {
        $target = Join-Path $folder $definition.Name
        $shortcut = $shell.CreateShortcut($target)
        $shortcut.TargetPath = $Pyw
        $shortcut.Arguments = $definition.Arguments
        $shortcut.WorkingDirectory = $Root
        $shortcut.IconLocation = "$Icon,0"
        $shortcut.WindowStyle = 1
        $shortcut.Description = $definition.Description
        $shortcut.Save()
        Write-Host "바로가기 생성: $target"
    }
}

Write-Host "설치 완료. 'NotEditor' 또는 'NotEditor 로컬 웹'으로 실행하세요." -ForegroundColor Green
