; Inno Setup script -> WaferGuard-Setup-1.0.0.exe   (run by scripts/build_desktop_windows.ps1)
#define AppVersion "1.0.0"
[Setup]
AppId={{6E3C8F4A-7B1D-4C55-9E0A-2F1B6C9D4A11}
AppName=WaferGuard
AppVersion={#AppVersion}
AppPublisher=WaferGuard
DefaultDirName={autopf}\WaferGuard
DefaultGroupName=WaferGuard
OutputDir=..\dist
OutputBaseFilename=WaferGuard-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern

[Files]
Source: "..\dist\WaferGuard\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\WaferGuard"; Filename: "{app}\WaferGuard.exe"
Name: "{autodesktop}\WaferGuard"; Filename: "{app}\WaferGuard.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Run]
Filename: "{app}\WaferGuard.exe"; Description: "Start WaferGuard"; Flags: nowait postinstall skipifsilent
