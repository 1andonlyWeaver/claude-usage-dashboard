"""The installer agrees with the app on every name they share (packaging/installer.iss)."""
import re
import sys
from pathlib import Path

import autostart
import instance
import paths

REPO = Path(__file__).parent.parent
ISS = (REPO / "packaging" / "installer.iss").read_text(encoding="utf-8")


def setup_value(key):
    match = re.search(rf"^{key}=(.*)$", ISS, re.MULTILINE)
    assert match, key
    return match.group(1).strip()


def define(name):
    match = re.search(rf'^#define {name} "(.*)"$', ISS, re.MULTILINE)
    assert match, name
    return match.group(1)


def iss_string(value):
    """An Inno Setup field: outer quotes dropped and doubled quotes undone when quoted."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1].replace('""', '"')
    return value


def run_entry():
    line = next(l for l in ISS.splitlines() if l.startswith("Root: HKCU"))
    return {key: iss_string(value) for key, value in re.findall(r'(\w+): ("(?:[^"]|"")*"|[^;]*)', line)}


def test_it_installs_per_user_without_admin_rights():
    assert setup_value("PrivilegesRequired") == "lowest"
    assert setup_value("DefaultDirName") == r"{localappdata}\Programs\{#AppName}"
    assert define("AppName") == "Claude Usage Dashboard"


def test_the_installer_waits_for_the_apps_mutex():
    assert setup_value("AppMutex") == instance.MUTEX_NAME


def test_the_installer_writes_the_run_value_the_app_reads_back(monkeypatch):
    entry = run_entry()
    assert entry["Subkey"] == autostart.RUN_KEY
    assert entry["ValueName"] == autostart.VALUE_NAME
    assert entry["Tasks"] == "startatlogin"
    app_dir = r"C:\Users\someone\AppData\Local\Programs\Claude Usage Dashboard"
    written = entry["ValueData"].replace("{app}", app_dir).replace("{#AppExe}", define("AppExe"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", app_dir + "\\" + define("AppExe"))
    assert written == autostart.command()


def test_a_first_install_decides_start_at_login_and_the_uninstaller_clears_it():
    task = next(l for l in ISS.splitlines() if l.startswith('Name: "startatlogin"'))
    assert task.endswith("Check: FirstInstall")
    code = ISS.split("[Code]", 1)[1]
    assert "function InitializeSetup: Boolean;" in code
    assert 'Uninstall\\{#SetupSetting("AppId")}_is1' in code
    assert f"RunKey = '{autostart.RUN_KEY}';" in code
    assert f"ApprovedKey = '{autostart.APPROVED_KEY}';" in code
    assert f"RunValue = '{autostart.VALUE_NAME}';" in code
    install, uninstall = code.split("procedure CurUninstallStepChanged", 1)
    assert "(CurStep = ssPostInstall) and not Upgrading" in install
    assert "WizardIsTaskSelected('startatlogin')" in install
    assert "RegDeleteValue(HKEY_CURRENT_USER, ApprovedKey, RunValue)" in install
    assert "RegDeleteValue(HKEY_CURRENT_USER, RunKey, RunValue)" in install
    assert "RegDeleteValue(HKEY_CURRENT_USER, RunKey, RunValue)" in uninstall
    assert "RegDeleteValue(HKEY_CURRENT_USER, ApprovedKey, RunValue)" in uninstall


def test_the_uninstaller_asks_before_deleting_the_apps_data(monkeypatch):
    assert "ExpandConstant('{localappdata}\\" + paths.APP_NAME + "')" in ISS
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Local")
    assert str(paths._app_home()) == "C:\\Local\\" + paths.APP_NAME  # the folder the app writes to
    assert "MB_YESNO or MB_DEFBUTTON2" in ISS  # No is the default answer
    assert "not UninstallSilent" in ISS
    assert "if not DelTree(DataDir, True, True, True) then" in ISS  # and says so when files were in use


def test_the_installer_packs_the_build_the_spec_makes():
    assert define("AppExe") == "ClaudeUsageDashboard.exe"
    assert r'Source: "..\dist\ClaudeUsageDashboard\*"' in ISS
    assert setup_value("OutputDir") == r"..\dist"
    assert setup_value("OutputBaseFilename") == "ClaudeUsageDashboard-Setup-{#AppVersion}"
    assert setup_value("SetupIconFile") == r"..\build\assets\icon.ico"
    assert setup_value("AppId") == "{{2E7A6516-90C1-4991-8014-641D5A440BAF}"


def test_install_update_and_setup_agree_on_the_switches(monkeypatch, tmp_path):
    import updates

    started = []
    monkeypatch.setattr(updates, "_popen", lambda args, **kw: started.append(args))
    updates.launch(tmp_path / "Setup.exe", "window")
    switches = dict(arg.split("=", 1) for arg in started[0][1:] if "=" in arg)
    code = ISS.split("[Code]", 1)[1]
    for name in switches.keys() - {"/LOG"}:  # /LOG is Inno Setup's own
        assert "{param:" + name[1:] + "|" in code, name
    assert switches["/UPDATE"] == "window"
    assert "if Mode = 'background' then" in code and "RelaunchArgs := '--background'" in code
    assert {"/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"} <= set(started[0])


def test_an_update_waits_for_the_app_ends_it_if_it_hangs_and_starts_it_again():
    code = ISS.split("[Code]", 1)[1]
    init = code.split("function InitializeSetup: Boolean;", 1)[1].split("procedure DeinitializeSetup", 1)[0]
    # AppMutex is checked after InitializeSetup, so waiting here keeps a silent Setup from giving up
    assert "WaitForApp(StrToIntDef(ExpandConstant('{param:PID|0}'), 0));" in init
    assert "'InstallLocation'" in init and "+ '{#AppExe}'" in init
    wait = code.split("procedure WaitForApp(Pid: Integer);", 1)[1].split("function InitializeSetup", 1)[0]
    assert "WaitForSingleObject(Process, QuitWaitMs) = WAIT_TIMEOUT" in wait
    assert "TerminateProcess(Process, 1);" in wait and "CloseHandle(Process);" in wait
    assert "QuitWaitMs = 30000;" in code
    relaunch = code.split("procedure DeinitializeSetup;", 1)[1].split("procedure CurStepChanged", 1)[0]
    assert "Exec(RelaunchExe, RelaunchArgs, '', SW_SHOWNORMAL, ewNoWait, ResultCode)" in relaunch
    # The wizard's own "Launch" box stays for interactive installs only
    run = next(l for l in ISS.splitlines() if l.startswith('Filename: "{app}\\{#AppExe}"'))
    assert "skipifsilent" in run
