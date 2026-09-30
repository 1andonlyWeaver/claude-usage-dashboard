# Claude Usage Dashboard

A dashboard for your Claude Code usage on Windows. It shows how much of your plan's 5-hour and weekly limits you've used, where your tokens went by day, project and model, what the same usage would cost at API prices, and which sessions were the heavy ones.

It runs on your own PC and serves the page from 127.0.0.1.

## What it reads, and what leaves your PC

It reads Claude Code's session logs in `%USERPROFILE%\.claude\projects`, the same folder inside any WSL distro that's running, and Claude Desktop's agent-mode logs in `%APPDATA%\Claude\local-agent-mode-sessions`. From those it copies token counts, model names, timestamps, project names and git branches into a local database. It doesn't keep the text of your conversations.

It also reads your Claude Code sign-in token from `%USERPROFILE%\.claude\.credentials.json`. The token only goes to Anthropic: to `api.anthropic.com` to read your quota percentages, and to `claude.ai` when you ask for it to be renewed. Unless you ask for a renewal, the dashboard never changes that file.

Nothing else leaves your PC, and there's no analytics or telemetry. The optional person-hours estimates described below also send summaries to Claude, through your own Claude Code.

## What you need

- Windows 10 or 11.
- Claude Code, signed in with your Claude account rather than an API key. The usage charts work from the logs alone, but the quota gauges need the account sign-in and stay empty without it.

## Running it

The first installer will come with release 2.0.0. Until then, run it from source with Python 3.12:

```
conda env create -f environment.yml
conda activate claude-usage-dashboard
python app.py --port 8080
```

Without conda, `pip install -r requirements.txt` followed by `python app.py --port 8080` works too.

Then open http://127.0.0.1:8080/. The first start reads every log you have, so give it a minute or two if you've used Claude Code a lot.

## When your sign-in needs attention

If the dashboard can't read your quota, a banner at the top says why and offers a fix.

- Not signed in, or the sign-in expired: click **Sign in**. A console window opens with `claude auth login`. Once you finish there, the dashboard picks up the new sign-in within a few seconds.
- The token expired: this is normal after a night away. It renews the next time you use Claude Code, or you can click **Renew now**.

Settings (the gear icon) can renew the token automatically. That's off by default: if the dashboard and Claude Code renew at the same moment, one of them loses, and you'd have to sign in again.

## Person-hours estimates (off by default)

The cost card can show roughly how long your work would have taken a person without AI. Turn on **Estimate person-hours** in Settings to get them. The dashboard then has Claude (Sonnet, through your Claude Code sign-in) read a summary of each day's sessions and estimate the hours.

That costs quota from your plan: about 1–2% of a week's quota to catch up on the last 90 days, then about 0.1–0.2% a week. It never bills an API key, even if `ANTHROPIC_API_KEY` is set on your PC. Claude's short summary of each day's work is stored with its estimate.

## Where your data lives

Running from source, it all stays in the repo folder. `data\` holds the database, a quota cache and your settings, and `logs\dashboard.log` is the server's log. The installed app will keep the same files under `%LOCALAPPDATA%\ClaudeUsageDashboard`.

Think twice before deleting `data\usage.db`. The dashboard rebuilds it from your logs on the next start, but Claude Code deletes logs older than 30 days by default, so anything older is gone for good, person-hours estimates included.

## If something's wrong

Open Settings and click **Copy diagnostics**, then send the text to whoever gave you the dashboard. It says what state the connection is in, when your token expires, when the quota was last read, and where the dashboard found Claude Code. It never includes the token.

## License

MIT, see `LICENSE`. The dashboard ships with Chart.js 4.4.4 (MIT) and the Sora, DM Sans and DM Mono fonts (SIL Open Font License 1.1). Their licenses sit next to them in `static/vendor` and `static/fonts`.
