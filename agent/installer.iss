[Setup]
AppId=PrintSetuShopConnector
AppName=PrintSetu Shop Connector
AppVersion=1.0.0
AppPublisher=PrintSetu
DefaultDirName={localappdata}\Programs\PrintSetu
DefaultGroupName=PrintSetu
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=PrintSetu-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\PrintSetu-Connector.exe
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\PrintSetu-Connector.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\PrintSetu Shop Connector"; Filename: "{app}\PrintSetu-Connector.exe"
Name: "{group}\Uninstall PrintSetu"; Filename: "{uninstallexe}"
Name: "{userdesktop}\PrintSetu"; Filename: "{app}\PrintSetu-Connector.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "PrintSetu"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\PrintSetu-Connector.exe"; Description: "Open PrintSetu Shop Connector"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{%USERPROFILE}\.printsetu-agent.json"
