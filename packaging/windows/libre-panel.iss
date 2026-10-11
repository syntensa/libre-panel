; Windows installer for Libre Panel (Inno Setup 6).
;
;   iscc /DAppVersion=0.1.0 /DNumericVersion=0.1.0.0 /DSourceDir=..\..\dist\libre-panel
;        /DOutputDir=..\..\dist packaging\windows\libre-panel.iss
;
; Installs for all users to Program Files by default: the sensor service (block "Sensor
; service" below) runs from a folder only administrators can change. "Install for me only"
; (or /CURRENTUSER) installs per user without administrator rights to
; %LOCALAPPDATA%\Programs\Libre Panel, as before, and leaves the system parts to the app's
; "Unlock all sensors". An update keeps the way the version before was installed.
; Libre Panel itself always runs without administrator rights.
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
; (sensor service) all users by default; "for me only" without administrator rights
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
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

; --- The programs that come with the download: ffmpeg, PresentMon, PawnIO's installer -------
; They are in the program folder's "tools" subfolder (packaging/fetch_tools.py, the PyInstaller
; spec), so the [Files] line above installs them with everything else and the uninstaller
; removes them with it. An update clears the folder first, so that a program a later version
; no longer carries goes with it. Their licence texts and sources are in tools\licenses and
; tools\SOURCES.txt (NOTICE.md says the same).
Type: filesandordirs; Name: "{app}\tools"

[Icons]
Name: "{autoprograms}\Libre Panel"; Filename: "{app}\LibrePanel.exe"
Name: "{autodesktop}\Libre Panel"; Filename: "{app}\LibrePanel.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\libre-panel.exe"; Parameters: "autostart enable"; Flags: runhidden runasoriginaluser; Tasks: autostart
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

[CustomMessages]
; -- Sensor service -------------------------------------------------------------------------
; What the sensors need from Windows, set up by the command that the portable download's
; "Unlock all sensors" runs too (libre_panel/sensorservice/setup.py): the sensor service
; LibrePanelSensors (LocalSystem, reads only), the PawnIO driver from {app}\tools, and the
; group Performance Log Users for game frame rates. All checked; for all users only.
; (This block runs to the end of the file; its [Code] holds Pascal, no ";" comments.)
en.SystemParts=Set up for all sensors (with the administrator rights this setup has):
de.SystemParts=Für alle Sensoren einrichten (mit den Administratorrechten dieses Setups):
en.SensorService=Sensor service: reads CPU, GPU and mainboard temperatures, power, fan speeds, memory and drives with the rights this needs; the app itself keeps running without administrator rights
de.SensorService=Sensordienst: liest die Temperaturen von CPU, GPU und Mainboard, Leistung, Lüfterdrehzahlen, Arbeitsspeicher und Laufwerke mit den Rechten, die das braucht; die App selbst läuft weiter ohne Administratorrechte
en.PawnIO=PawnIO driver (needed by the sensor service; signed, open source)
de.PawnIO=PawnIO-Treiber (braucht der Sensordienst; signiert, quelloffen)
en.PerfLogUsers=Game frame rates: adds you to Performance Log Users (after signing in again)
de.PerfLogUsers=Bildraten in Spielen: nimmt dich in die Leistungsprotokollbenutzer auf (nach erneuter Anmeldung)
en.SettingUpSensors=Setting up what the sensors need...
de.SettingUpSensors=Einrichten, was die Sensoren brauchen ...

[Tasks]
Name: "sensorservice"; Description: "{cm:SensorService}"; GroupDescription: "{cm:SystemParts}"; Check: IsAdminInstallMode
Name: "pawnio"; Description: "{cm:PawnIO}"; GroupDescription: "{cm:SystemParts}"; Check: IsAdminInstallMode
Name: "perflogusers"; Description: "{cm:PerfLogUsers}"; GroupDescription: "{cm:SystemParts}"; Check: IsAdminInstallMode

[InstallDelete]
Type: filesandordirs; Name: "{app}\sensors\_internal"

[Run]
; PawnIO first: the service looks for it when it starts
Filename: "{app}\libre-panel.exe"; Parameters: "system-setup --pawnio"; StatusMsg: "{cm:SettingUpSensors}"; Flags: runhidden; Tasks: pawnio
Filename: "{app}\libre-panel.exe"; Parameters: "system-setup --service"; StatusMsg: "{cm:SettingUpSensors}"; Flags: runhidden; Tasks: sensorservice
Filename: "{app}\libre-panel.exe"; Parameters: "system-setup --perf-log-users"; StatusMsg: "{cm:SettingUpSensors}"; Flags: runhidden; Tasks: perflogusers

[UninstallRun]
; the service and the group membership that system-setup gave (PawnIO stays: other programs
; use it too). (A Check of [UninstallRun] is asked at install time: the install mode.)
Filename: "{app}\libre-panel.exe"; Parameters: "system-setup --remove --service --perf-log-users"; Flags: runhidden; RunOnceId: "RemoveSensorService"; Check: IsAdminInstallMode

[UninstallDelete]
; the service's log, which it wrote next to its program
Type: filesandordirs; Name: "{app}\sensors\logs"

[Code]
function SensorServiceInstalled: Boolean;
begin
  Result := RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\LibrePanelSensors');
end;

{ A "for me only" install whose app unlocked the sensors: removing what that set up needs
  administrator rights, so Windows asks for them (not in a silent uninstall). }
<event('CurUninstallStepChanged')>
procedure RemoveSensorServiceForMeOnly(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if (CurUninstallStep = usUninstall) and (not IsAdminInstallMode) and (not UninstallSilent)
     and SensorServiceInstalled then
    ShellExec('runas', ExpandConstant('{app}\libre-panel.exe'),
              'system-setup --remove --service --perf-log-users', '', SW_HIDE,
              ewWaitUntilTerminated, ResultCode);
end;

{ An update: the service runs from the folder "sensors" of the program, so it stops before
  its files are replaced (the setup's system-setup --service starts it again). }
<event('PrepareToInstall')>
function StopSensorService(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  if IsAdminInstallMode and SensorServiceInstalled then
    Exec(ExpandConstant('{sys}\net.exe'), 'stop LibrePanelSensors', '', SW_HIDE,
         ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
