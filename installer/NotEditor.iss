; 버전은 릴리스 워크플로가 깃 태그에서 정해 /DAppVersion 으로 넘긴다.
; 아래 값은 그것 없이 수동 실행했을 때만 쓰이며, 진짜 버전이 아님이 드러나야 한다.
#ifndef AppVersion
  #define AppVersion "0.0.0-manual"
#endif

[Setup]
AppId={{8ECA7EDB-24EA-4DF1-9786-E0D334F01AD5}
AppName=NotEditor
AppVersion={#AppVersion}
AppPublisher=NotEditor
DefaultDirName={autopf}\NotEditor
DefaultGroupName=NotEditor
OutputDir=..\release
OutputBaseFilename=NotEditor-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\NotEditor.exe
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\NotEditor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\NotEditor"; Filename: "{app}\NotEditor.exe"
Name: "{autodesktop}\NotEditor"; Filename: "{app}\NotEditor.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "바탕화면 바로가기 만들기"; GroupDescription: "추가 바로가기:"; Flags: checkedonce

[Run]
Filename: "{app}\NotEditor.exe"; Description: "NotEditor 실행"; Flags: nowait postinstall skipifsilent
