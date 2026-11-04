#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceExe
  #error SourceExe must point to CyberMiao-Portable-x64.exe
#endif
#ifndef OutputDir
  #error OutputDir must be provided by desktop/build.py
#endif

[Setup]
AppId=CyberMiao.CyberMiao.Desktop
AppName=赛博小喵 Cyber Miao
AppVersion={#AppVersion}
AppVerName=赛博小喵 Cyber Miao {#AppVersion}
AppPublisher=赛博小喵 Cyber Miao
DefaultDirName={localappdata}\Programs\CyberMiao
DefaultGroupName=赛博小喵 Cyber Miao
DisableProgramGroupPage=yes
UninstallDisplayName=赛博小喵 Cyber Miao {#AppVersion}
UninstallDisplayIcon={app}\CyberMiao.exe
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
OutputDir={#OutputDir}
OutputBaseFilename=CyberMiao-Setup-x64
SetupIconFile=..\assets\icon.ico
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
ChangesAssociations=no

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式:"; Flags: unchecked

[Files]
Source: "{#SourceExe}"; DestDir: "{app}"; DestName: "CyberMiao.exe"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\赛博小喵 Cyber Miao"; Filename: "{app}\CyberMiao.exe"
Name: "{autodesktop}\赛博小喵 Cyber Miao"; Filename: "{app}\CyberMiao.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\CyberMiao.exe"; Description: "启动赛博小喵 Cyber Miao"; Flags: postinstall nowait skipifsilent
