# Claude Usage Dashboard

A dashboard for your Claude Code usage on Windows. It shows how much of your plan's 5-hour and weekly limits you've used, where your tokens went by day, project and model, what the same usage would cost at API prices, and which sessions were the heavy ones.

It runs on your own PC, in its own window, with a tray icon that keeps it going after you close the window.

## What it reads, and what leaves your PC

It reads Claude Code's session logs in `%USERPROFILE%\.claude\projects`, the same folder inside any WSL distro that's running, and Claude Desktop's agent-mode logs in `%APPDATA%\Claude\local-agent-mode-sessions`. From those it copies token counts, model names, timestamps, project names and git branches into a local database. It doesn't keep the text of your conversations.

It also reads your Claude Code sign-in from `%USERPROFILE%\.claude\.credentials.json`. The access token goes to `api.anthropic.com` to read your quota percentages. The refresh token goes to `claude.ai`, and only when the dashboard renews your sign-in: when you click **Renew now**, or by itself if you turn on automatic renewal. A renewal is the only thing that changes that file.

Once a day it asks GitHub for the number of the latest release, so it can tell you about new versions. You can turn that off in Settings.

Nothing else leaves your PC, and there's no analytics or telemetry. The optional person-hours estimates described below also send Claude an extract of each day's sessions, through your own Claude Code: your prompts and Claude's last replies, both clipped, plus the paths of files it changed and the descriptions of shell commands it ran.

## What you need

- Windows 10 or 11, 64-bit.
- Claude Code, signed in with your Claude account rather than an API key. The usage charts work from the logs alone, but the quota gauges need the account sign-in and stay empty without it.
- Microsoft's WebView2 runtime, for the window. Windows 11 has it, and so do most Windows 10 PCs with a current Edge. Without it, the dashboard opens in your browser and everything else works the same.

## Installing

Download `ClaudeUsageDashboard-Setup-<version>.exe` from the [latest release](https://github.com/1andonlyWeaver/claude-usage-dashboard/releases/latest) and run it. It installs for your Windows account only, in `%LOCALAPPDATA%\Programs\Claude Usage Dashboard`, so it doesn't need admin rights.

The installer isn't signed, so Windows will probably stop it with "Windows protected your PC". Click **More info**, check that the app is `ClaudeUsageDashboard-Setup-<version>.exe`, then click **Run anyway**. Antivirus programs are sometimes wary of apps packaged the way this one is (with PyInstaller). If yours quarantines it, that's the likely reason.

The installer can start the dashboard each time you sign in to Windows. Leave that ticked and it waits in the tray with its window closed. The first start reads every log you have, so give it a minute or two if you've used Claude Code a lot.

## Using it

Closing the window only hides it. The tray icon brings it back; it may be hiding under the ^ arrow next to the clock. Its menu also opens the dashboard in your browser, turns **Start at login** on or off, checks for updates, and quits. Starting the app again while it runs just brings its window forward.

Ctrl+plus and Ctrl+minus zoom the window, and Ctrl+0 puts it back.

## Updating

When there's a newer release, a notice at the top of the dashboard links to it. Download the new installer and run it over the old one. Your data, your settings and your Start at login choice stay. If the installer says the dashboard is running, choose **Quit** from the tray icon's menu, then click **OK**.

**Check for updates** in the tray menu, or **Check now** in Settings, asks GitHub straight away.

## Uninstalling

Uninstall it from **Installed apps** in Windows Settings (**Apps & features** on Windows 10). The uninstaller turns off Start at login and asks whether to delete your data too. Say no and a later install picks up where you left off.

## When your sign-in needs attention

If the dashboard can't read your quota, a banner at the top says why and offers a fix.

- Not signed in, or the sign-in expired: click **Sign in**. A console window opens with `claude auth login`. Once you finish there, the dashboard picks up the new sign-in within a few seconds.
- The token expired: this is normal after a night away. It renews the next time you use Claude Code, or you can click **Renew now**.

The tray icon also gets an amber dot, and Windows shows one notification, when you need to sign in or install Claude Code.

Settings (the gear icon) can renew the token automatically. That's off by default: if the dashboard and Claude Code renew at the same moment, one of them loses, and you'd have to sign in again.

## Person-hours estimates (off by default)

The cost card can show roughly how long your work would have taken a person without AI. Turn on **Estimate person-hours** in Settings to get them. The dashboard then has Claude (Sonnet, through your Claude Code sign-in) read that extract of each day's sessions and estimate the hours.

That costs quota from your plan: about 1–2% of a week's quota to catch up on the last 90 days, then about 0.1–0.2% a week. The dashboard runs Claude Code without `ANTHROPIC_API_KEY` and the other variables that would send the bill to an API account or a cloud provider. It can't see a key that Claude Code gets from its own settings, though: an `apiKeyHelper`, or an `env` block in `%USERPROFILE%\.claude\settings.json`. If you've set one of those up, the estimates are billed to that key. Claude's short summary of each day's work is stored with its estimate.

## Where your data lives

Everything the dashboard keeps is in `%LOCALAPPDATA%\ClaudeUsageDashboard`. Its `data` folder holds the database, a quota cache, your settings, the last update check (`update.json`), the port of the running copy (`runtime.json`) and the window's own browser storage (`webview`). Its `logs` folder holds `dashboard.log`.

Think twice before deleting `data\usage.db`. The dashboard rebuilds it from your logs on the next start, but Claude Code deletes logs older than 30 days by default, so anything older is gone for good, person-hours estimates included.

## If something's wrong

Open Settings and click **Copy diagnostics**, then send the text to whoever gave you the dashboard. It says what state the connection is in, when your token expires, when the quota was last read, and where the dashboard found Claude Code. It never includes the token. The log, `%LOCALAPPDATA%\ClaudeUsageDashboard\logs\dashboard.log`, helps too.

## Running from source

This is for working on the dashboard itself. You need Python 3.12:

```
conda env create -f environment.yml
conda activate claude-usage-dashboard
python desktop.py
```

`python app.py --port 8080` runs the server on its own, for your browser at http://127.0.0.1:8080/. Without conda, `pip install -r requirements.txt` works too.

From source, the data stays in the repo's `data` folder. The server prints its log to the console you started it from, or to `logs\dashboard.log` when started without one (with `pythonw`). Quit the installed app before running `desktop.py`: only one copy runs per Windows session. And don't run two copies with automatic renewal on, installed or not, or they'll race to renew your token.

To build the installer yourself, install the build tools with `pip install -r packaging/requirements-build.txt` and get [Inno Setup 6](https://jrsoftware.org/isdl.php). Then, from the repo folder, with the number from `version.py` (`ISCC.exe` is in Inno Setup's install folder, which isn't on `PATH`):

```
python -m PyInstaller --noconfirm --clean packaging/ClaudeUsageDashboard.spec
ISCC /DAppVersion=2.0.0 packaging\installer.iss
```

The installer lands in `dist`. Releases themselves are built by GitHub Actions from a version tag.

## License

MIT, see `LICENSE`. The dashboard ships with Chart.js 4.4.4 (MIT) and the Sora, DM Sans and DM Mono fonts (SIL Open Font License 1.1). Their licenses sit next to them in `static/vendor` and `static/fonts`.
