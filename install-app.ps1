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
$targets = @(
    (Join-Path ([Environment]::GetFolderPath("Programs")) "NotEditor.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "NotEditor.lnk")
)

foreach ($target in $targets) {
    $shortcut = $shell.CreateShortcut($target)
    $shortcut.TargetPath = $Pyw
    $shortcut.Arguments = "-m noteditor"
    $shortcut.WorkingDirectory = $Root
    $shortcut.IconLocation = "$Icon,0"
    $shortcut.WindowStyle = 1
    $shortcut.Description = "PDF 문서 합치기와 Samsung Notes 필기 옮기기"
    $shortcut.Save()
    Write-Host "바로가기 생성: $target"
}

Write-Host "설치 완료. 시작 메뉴 또는 바탕화면의 'NotEditor'로 실행하세요." -ForegroundColor Green
