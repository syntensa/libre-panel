; Windows installer for Libre Panel (Inno Setup 6).
;
;   iscc /DAppVersion=0.1.0 /DNumericVersion=0.1.0.0 /DSourceDir=..\..\dist\libre-panel
;        /DOutputDir=..\..\dist packaging\windows\libre-panel.iss
;
; Installs per user (no administrator rights) to %LOCALAPPDATA%\Programs\Libre Panel.
; Autostart is registered by Libre Panel itself (`autostart enable`), so the
; installer, the tray menu and the editor all manage the same entry.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef NumericVersion
  #define NumericVersion "0.0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\libre-panel"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

[Setup]
AppId={{76267DAF-70B8-4D39-B8D9-C1C17EB0FF73}
AppName=Libre Panel
AppVersion={#AppVersion}
AppVerName=Libre Panel {#AppVersion}
AppPublisher=Libre Panel contributors
AppPublisherURL=https://github.com/syntensa/libre-panel
AppSupportURL=https://github.com/syntensa/libre-panel/issues
VersionInfoVersion={#NumericVersion}
DefaultDirName={autopf}\Libre Panel
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=libre-panel-{#AppVersion}-windows-x64-setup
SetupIconFile=..\icons\libre-panel.ico
UninstallDisplayIcon={app}\LibrePanel.exe
UninstallDisplayName=Libre Panel
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[CustomMessages]
en.Autostart=Start Libre Panel when I log in
de.Autostart=Libre Panel bei der Anmeldung starten
en.StartNow=Start Libre Panel now
de.StartNow=Libre Panel jetzt starten

[Tasks]
Name: "autostart"; Description: "{cm:Autostart}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Files of an older version that the new one no longer has are removed with it.
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\Libre Panel"; Filename: "{app}\LibrePanel.exe"
Name: "{autodesktop}\Libre Panel"; Filename: "{app}\LibrePanel.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\libre-panel.exe"; Parameters: "autostart enable"; Flags: runhidden; Tasks: autostart
Filename: "{app}\LibrePanel.exe"; Description: "{cm:StartNow}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\libre-panel.exe"; Parameters: "quit"; Flags: runhidden; RunOnceId: "QuitLibrePanel"
Filename: "{app}\libre-panel.exe"; Parameters: "autostart disable"; Flags: runhidden; RunOnceId: "RemoveAutostart"

[Code]
{ Quit a running Libre Panel before its files are replaced (updates). }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  if FileExists(ExpandConstant('{app}\libre-panel.exe')) then
    Exec(ExpandConstant('{app}\libre-panel.exe'), 'quit', '', SW_HIDE,
         ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
