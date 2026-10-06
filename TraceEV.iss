; The Windows installer: TraceEV.exe, a Start menu entry, an uninstaller.
;
; Built by `python build.py` on Windows when Inno Setup is installed, and by
; the build workflow on every tag. The version comes from the command line
; (/DVersion=v0.1.3-beta); a hand build without one is "dev".
;
; Installed per user, into %LOCALAPPDATA%\Programs\TraceEV, with no
; administrator prompt. Not for tidiness: the program keeps hands.db and
; TraceEV.log beside itself, and under Program Files it could not write
; either -- the first launch would fail to make its database, which is the
; failure v0.1.0-beta shipped with on 6 Oct 2026.
;
; Uninstalling removes what was installed and nothing else. hands.db, the
; log and the user's own stats, filters and views files were never installed,
; so they stay: they are the user's hands, and reinstalling finds them again.

#ifndef Version
  #define Version "dev"
#endif

[Setup]
; The id is what makes a later installer an upgrade of this one rather than
; a second copy beside it. It must never change.
AppId={{6C1F0E2A-3B7D-4C59-9A41-2E8D5F7B1C03}
AppName=TraceEV
AppVersion={#Version}
AppVerName=TraceEV {#Version}
AppPublisher=TraceEV
AppPublisherURL=https://github.com/bhntr81/traceEV
AppSupportURL=https://github.com/bhntr81/traceEV/issues
AppUpdatesURL=https://github.com/bhntr81/traceEV/releases
DefaultDirName={localappdata}\Programs\TraceEV
DefaultGroupName=TraceEV
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=TraceEV-setup
SetupIconFile=TraceEV.ico
UninstallDisplayIcon={app}\TraceEV.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; A running TraceEV holds its own .exe open, and an upgrade over it would
; fail half way; this asks to close it first.
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\TraceEV.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\TraceEV"; Filename: "{app}\TraceEV.exe"
Name: "{autodesktop}\TraceEV"; Filename: "{app}\TraceEV.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TraceEV.exe"; Description: "{cm:LaunchProgram,TraceEV}"; Flags: nowait postinstall skipifsilent
