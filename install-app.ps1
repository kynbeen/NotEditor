$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Pyw = Join-Path $Root "venv\Scripts\pythonw.exe"
$Icon = Join-Path $Root "assets\icon.ico"

if (-not (Test-Path -LiteralPath $Pyw)) {
    throw "먼저 setup.ps1을 실행하세요: $Pyw 없음"
}
# 코드와 함께 아이콘이 바뀔 수 있으므로 기존 파일이 있어도 매번 다시 만든다.
& (Join-Path $Root "venv\Scripts\python.exe") -m pdf_page_composer.make_icon

$shell = New-Object -ComObject WScript.Shell
$targets = @(
    (Join-Path ([Environment]::GetFolderPath("Programs")) "PDF 페이지 조합기.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "PDF 페이지 조합기.lnk")
)

foreach ($target in $targets) {
    $shortcut = $shell.CreateShortcut($target)
    $shortcut.TargetPath = $Pyw
    $shortcut.Arguments = "-m pdf_page_composer"
    $shortcut.WorkingDirectory = $Root
    $shortcut.IconLocation = "$Icon,0"
    $shortcut.WindowStyle = 1
    $shortcut.Description = "필요한 PDF 페이지만 골라 하나로 조합"
    $shortcut.Save()
    Write-Host "바로가기 생성: $target"
}

Write-Host "설치 완료. 시작 메뉴 또는 바탕화면의 'PDF 페이지 조합기'로 실행하세요." -ForegroundColor Green
