#ifndef AppVersion
  #define AppVersion "0.4.0"
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
