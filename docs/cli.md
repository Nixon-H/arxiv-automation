# CLI Reference

`python run.py <mode>` — modes are mutually exclusive.

| Flag | Description |
|------|-------------|
| `--dry-run` | Preview next dispatch without sending (writes `preview.html`) |
| `--test [EMAIL]` | Send a test email (default `test@example.com`) |
| `--live` | Send one live email |
| `--live --send N` | Send a batch of N emails |
| `--followups [DAYS]` | List no-reply recipients due for follow-up (default 7 days) |
| `--followups DAYS --send N` | Send follow-ups to the first N candidates |
| `--mark-replied EMAIL` | Mark recipient as replied (stops follow-ups) |
| `--retry-failed` | Retry previously failed dispatches |
| `--stats` | Execution statistics + domain reputation table |
| `--verify` | Pre-flight validation |
| `--doctor [BUNDLE]` | Full system diagnostic; `--doctor=bundle` → `diagnostics.zip` |
| `--validate-config` | Validate configuration |
| `--export-report` | Export JSON/CSV/HTML reports |
| `--reset-progress` | Reset progress tracking |
| `--scheduler` | Generate cron/systemd config |
| `--metrics [PORT]` | Prometheus metrics endpoint (default 9090) |
| `--init` | Interactive configuration wizard |

## Global options

| Flag | Description |
|------|-------------|
| `--send N` | Batch size for `--live` / `--followups` |
| `--locale LANG` | i18n locale (`en`, `fr`, `zh`) |
| `--no-browser` | Don't auto-open `preview.html` |
