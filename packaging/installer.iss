; Inno Setup script for Claude Usage Dashboard: a per-user install that needs no admin rights.
;
; Build the app first (packaging/ClaudeUsageDashboard.spec), then from the repo root:
;     ISCC /DAppVersion=2.0.0 packaging\installer.iss
; Output: dist\ClaudeUsageDashboard-Setup-2.0.0.exe
;
; Names shared with the app (tests/test_installer.py checks them):
; - AppMutex is instance.MUTEX_NAME, so Setup asks the person to quit a running copy first.
; - The Run value is what autostart.command() writes when frozen: "<exe>" --background.
;   With any other text the tray's Start at login checkbox would read off.
;   Start at login is offered on a first install only; upgrades leave it as the tray left it.
; - AppId must never change: it's how Setup finds an earlier install to upgrade.

#ifndef AppVersion
  #error Pass the release number: ISCC /DAppVersion=2.0.0 packaging\installer.iss
#endif
#define AppName "Claude Usage Dashboard"
#define AppExe "ClaudeUsageDashboard.exe"

[Setup]
AppId={{2E7A6516-90C1-4991-8014-641D5A440BAF}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Jonathan Weaver
AppPublisherURL=https://github.com/1andonlyWeaver/claude-usage-dashboard
AppSupportURL=https://github.com/1andonlyWeaver/claude-usage-dashboard
AppUpdatesURL=https://github.com/1andonlyWeaver/claude-usage-dashboard/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
AppMutex=Local\ClaudeUsageDashboard
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=ClaudeUsageDashboard-Setup-{#AppVersion}
SetupIconFile=..\build\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2
SolidCompression=yes

[Tasks]
Name: "startatlogin"; Description: "Start {#AppName} in the tray when I sign in to Windows"; Check: FirstInstall
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; The previous build's libraries: a stale one left beside the new ones can break the app
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\ClaudeUsageDashboard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Start at login, written exactly as autostart.command() writes it when frozen
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ClaudeUsageDashboard"; ValueData: """{app}\{#AppExe}"" --background"; Tasks: startatlogin; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  ApprovedKey = 'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run';
  RunValue = 'ClaudeUsageDashboard';

var
  Upgrading: Boolean;

{ An earlier install of this AppId is still registered: read once, before Setup writes its own entry }
function InitializeSetup: Boolean;
begin
  Upgrading := RegKeyExists(HKEY_CURRENT_USER,
    ExpandConstant('Software\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1'));
  Result := True;
end;

{ Start at login is offered on a first install only. An upgrade leaves it as the tray left it. }
function FirstInstall: Boolean;
begin
  Result := not Upgrading;
end;

{ On a first install the Start at login box decides. An upgrade leaves the Run value as it is. }
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and not Upgrading then
  begin
    if WizardIsTaskSelected('startatlogin') then
      { As when the tray turns it on: clear an "off" left by Task Manager's Startup tab }
      RegDeleteValue(HKEY_CURRENT_USER, ApprovedKey, RunValue)
    else
      RegDeleteValue(HKEY_CURRENT_USER, RunKey, RunValue);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    { The tray writes these too, so remove them whoever wrote them }
    RegDeleteValue(HKEY_CURRENT_USER, RunKey, RunValue);
    RegDeleteValue(HKEY_CURRENT_USER, ApprovedKey, RunValue);
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\ClaudeUsageDashboard');
    if DirExists(DataDir) and not UninstallSilent and
       (MsgBox('Also delete your dashboard data?' + #13#10#13#10 +
               'That''s the usage database, person-hours estimates, settings and logs in ' + DataDir + '. ' +
               'Keep them and a later install carries on where this one left off. ' +
               'Claude Code''s own logs and sign-in aren''t touched either way.',
               mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES) then
      DelTree(DataDir, True, True, True);
  end;
end;
