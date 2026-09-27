#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "YouTube Downloader"
#define AppExe "YouTube Downloader.exe"

[Setup]
AppId={{E123E2C0-4FF0-48DB-9031-726315815054}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Johnnyboy135
AppPublisherURL=https://github.com/Johnnyboy135/YT-downloader
DefaultDirName={localappdata}\Programs\YT-downloader
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DisableProgramGroupPage=yes
OutputDir=..\installer-output
OutputBaseFilename=YT-Downloader-Setup-{#AppVersion}-x64
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\YouTube Downloader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; AppUserModelID: "Johnnyboy135.YTDownloader"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon; AppUserModelID: "Johnnyboy135.YTDownloader"

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
