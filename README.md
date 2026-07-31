# arXiv Endorsement Dispatch Engine

Automated, production-grade email dispatch system for requesting arXiv endorsements. Handles the full pipeline — parsing endorser lists, rendering personalized emails, routing through multiple SMTP accounts with health-aware load balancing, retries, bounce handling, and deliverability analytics.

**Author:** Nixon-H

> **Disclaimer:** Use responsibly. Respect recipient privacy, follow anti-spam regulations (CAN-SPAM, GDPR), and only contact researchers who have indicated openness to endorsement requests.

---

## Features

### Core Dispatch Engine
- **Multi-account routing** — health-aware adaptive selection: picks the healthiest account by weighted success rate + average latency
- **Per-provider rate limiting** — gmail 20/h 200/d, outlook 15/h 150/d (configurable per account), enforced in-process + persisted in DB across restarts
- **SMTP connection pooling** — `SmtpConnectionPool` reuses authenticated connections, lazy reconnects on failure
- **SMTP capability detection** — after EHLO+STARTTLS+EHLO, inspects `esmtp_features`: STARTTLS, AUTH (with supported mechanisms listed: LOGIN/PLAIN/XOAUTH2...), SIZE, PIPELINING, 8BITMIME, SMTPUTF8, DSN. Exposed via `get_account_capabilities()` / `all_capabilities()`
- **Provider fingerprinting** — SMTP banner matched against 12 provider patterns (Google, M365/Exchange, Postfix, Exim, Sendmail, Zimbra, etc.) with domain-based fallback
- **Error classification** — 8-class taxonomy (`SmtpErrorClass`): authentication, permanent, temporary, timeout, rate-limited, DNS, hard/soft bounce, TLS, unknown — each with a recovery strategy
- **Randomized exponential backoff** with jitter (`2^n * rand(0.8, 1.5)`) between retries
- **Bounce processing** — 30+ SMTP response codes (421→554) mapped to actions + bounce types, regex text-pattern fallback, bounce DB table, hard-bounced recipients auto-skipped on future runs, recovery advice per bounce type
- **DNS validation** — MX lookup (dnspython w/ dig fallback), A-record fallback, disposable-email domain blacklist
- **Parallel DNS validation** — `validate_emails_parallel()` via ThreadPoolExecutor
- **Email fingerprinting** — SHA256 body hash stored per send to prevent duplicate sends
- **Correlation IDs** — `uuid4().hex[:12]` generated per send, threaded through the full pipeline (SMTP → DB → structured logs) so one send is traceable end-to-end

### Data & Parsing
- Multi-format input: **TXT / CSV / JSON / YAML / XLSX** with magic-byte + extension auto-detection (TXT supports block format or `Name | email | paper` pipe-separated lines)
- Unicode normalization (NFKC) on all name/paper fields
- **In-file duplicate detection** with per-category stats:
  - Duplicate email
  - Duplicate (name + paper)
  - Duplicate exact record hash
  - Reported as `Duplicates skipped — Email: N, Name/Paper: N, Exact: N`
- PDF header validation (`%PDF-` magic) for your manuscript
- File integrity + checksum verification (SHA256/MD5)
- Contact history per recipient (first/last contact, total sends, bounces)

### Template & Content Engine
- Multi-template rotation with **HTML + plain-text** pair support
- Subject-line rotation (anti-spam-signature diversity)
- Signature profiles with variants
- Auto plain-text generation from HTML (`strip_html`)
- Template caching with mtime-based auto-reload (`get_cache_stats()`)
- **Template linting** — greeting presence, lines >120 chars, multiple spaces, empty paragraphs
- **HTML sanity checks** — tag balance verification for 12 HTML tags
- **Render validation** — `get_required_vars()` + `validate_context()` ensures all `{{ var }}` placeholders resolve
- **Email quality scoring** — 0–100 composite score with A–F grade: missing subject/body, 30+ spam trigger words, broken links, attachment validation, lint + HTML issues — shown per email in dry-run and live sends
- Anti-spam hygiene: `X-Mailer`, `User-Agent`, `Reply-To`, custom headers (no bulk-mail `List-Unsubscribe` — deliberate, keeps personal outreach out of bulk classification)
- **Archive sent emails** — every successful send saved as `sent/YYYY-MM-DD_Name.eml` (full MIME structure)

### Safety & Reliability
- **File locking** (`fcntl.flock`) — prevents concurrent runs on the same state
- **Atomic SQLite transactions** — crash recovery without corruption, `VACUUM` support
- **Versioned schema migrations** — `schema_version` + `migration_history` tables (currently schema v4: latency_details, body_fingerprint + smtp_conversation, correlation_id migrations)
- Auto-backup of progress state (`data/backups/progress_*.json`)
- **Secret redaction in all logs** — 7 regex patterns + env-var auto-detection, `***REDACTED***`
- **Immutable config** — frozen dataclasses post-validation (`config_version: 2`, auto-upgrade from v1)
- **Encrypted credential store** — Fernet-encrypted `.credentials.enc` (cryptography lib)
- Pre-flight checks (`--verify`): templates, PDF, config, accounts, DNS
- **`--doctor` diagnostic (16+ checks)**: file existence, imports, writability, CLI tools, Python version, SMTP auth, **DKIM/SPF/DMARC** (SPF record, DKIM selector, DMARC policy via dig), TLS cert info (issuer/subject/expiry), attachment validation, template git-diff detection
- **Diagnostic bundle** — `--doctor=bundle` → `diagnostics.zip` (config, logs, DB, templates, plugins)
- **Interactive resume prompt** before batch sends ("Resume from #N? [Enter=yes, n=restart]")

### Observability
- Structured JSON logging (`.jsonl`) with severity, recipient, account, status, latency, correlation ID
- **Per-phase delivery latency** — DNS → SMTP connect → EHLO → TLS handshake → AUTH → DATA → TOTAL, stored as JSON in DB
- **Prometheus metrics endpoint** (`/metrics`) — counters, gauges, histograms (sends, failures, latency)
- **Rich HTML dashboard** — SVG pie charts (outcome distribution, duplicate breakdown), send-latency timeline (last 50 sends), account health table with success rates
- Export reports: JSON / CSV / HTML
- **Domain reputation report** — per-domain success rate + avg latency table in `--stats`
- Execution stats dashboard (`--stats`) with runtime counters

### Extensibility
- **Plugin architecture** — drop-in `plugins/` directory, auto-discovered, 6 lifecycle hooks:
  - `@hook_before_send` / `@hook_after_send`
  - `@hook_before_validate` / `@hook_after_failure`
  - `@hook_after_retry` / `@hook_before_archive`
- i18n (en / fr / zh) via JSON locale files, `--locale` flag
- Notifications: Discord / Slack / Desktop (notify-send / osascript / win10toast), completion summaries
- **Interactive setup wizard** — `--init` guides multi-account config creation
- **Named SMTP profiles** in config (multiple providers per account entry)
- Scheduler generation: cron line + systemd service/timer units
- **`arxiv-mail` CLI** — pip-installable entry point

### Developer Experience
- **100 tests** across 9 test files (unit + fuzz), 0.29s runtime
- **Fuzz testing** — random malformed TXT/CSV/JSON/binary/huge-lines/special-chars thrown at the parser
- **GitHub Actions CI** — ruff lint, mypy typecheck, test matrix (Python 3.10/3.11/3.12 × Ubuntu/macOS/Windows), coverage upload, bandit + pip-audit + safety security scanning
- **Pre-commit hooks** — ruff, mypy, bandit, yaml/json fixers
- **Makefile** — `make test / lint / typecheck / security / doctor / clean / all`
- **Semantic release** config with conventional-commit enforcement
- Docs: README, CHANGELOG, CONTRIBUTING (with architecture + sequence diagrams), SECURITY, MIT LICENSE

---

## Installation

```bash
git clone https://github.com/Nixon-H/arxiv-automation.git
cd arxiv_automation
pip install -e .
```

### Optional dependencies

```bash
pip install openpyxl      # XLSX parsing
pip install pyyaml        # YAML parsing
pip install dnspython     # DNS validation
pip install cryptography  # encrypted credential store
pip install prometheus-client  # metrics
```

---

## Quick Start

### 1. Configure

Edit `config.json`:

```json
{
    "sender_identity": {
        "your_name": "Nixon-H",
        "your_paper_title": "My Paper Title",
        "arxiv_category": "cs.AI"
    },
    "safety_and_limits": {
        "cooldown_hours": 24.0,
        "smtp_timeout_seconds": 30.0,
        "random_delay_range_seconds": [5, 15],
        "max_retries": 3,
        "initial_backoff_seconds": 2.0
    },
    "accounts": [
        {
            "provider": "gmail",
            "email": "you@gmail.com",
            "password": "${SMTP_PASSWORD_1}",
            "server": "smtp.gmail.com",
            "port": 587,
            "max_per_hour": 20,
            "max_per_day": 200
        }
    ],
    "config_version": 2
}
```

### 2. Set passwords

Create `.env` (gitignored):

```
SMTP_PASSWORD_1=your-gmail-app-password-here
```

Gmail requires an **App Password** (2-Step Verification must be enabled):
1. https://myaccount.google.com/security → enable 2-Step Verification
2. https://myaccount.google.com/apppasswords → create app password
3. Put it in `.env` (spaces removed)

### 3. Prepare your materials

- `endorsers.txt` — one record per endorser (see sample format below)
- `template.txt` / `template.html` — your email templates with `{{ var }}` placeholders
- `my_paper.pdf` — your manuscript (validated for PDF header)

### 4. Preview & test

```bash
python run.py --dry-run          # Preview next dispatch (renders + writes preview.html)
python run.py --verify           # Pre-flight validation checks
python run.py --test you@example.com  # Send a test email
```

### 5. Go live

```bash
python run.py --live             # Send one email
python run.py --live --send 5    # Send a batch of 5
python run.py --retry-failed     # Retry previously failed sends
```

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `--dry-run` | Preview next dispatch, generate `preview.html` |
| `--test [EMAIL]` | Send test email to verify configuration |
| `--live` | Execute live dispatch (use with `--send N`) |
| `--send N` | Batch size for `--live` |
| `--stats` | Display execution statistics + domain reputation table |
| `--verify` | Pre-flight validation |
| `--doctor` / `--doctor=bundle` | Full diagnostic / diagnostic zip bundle |
| `--export-report` | Generate JSON/CSV/HTML reports |
| `--validate-config` | Validate configuration file |
| `--retry-failed` | Retry failed dispatches |
| `--reset-progress` | Reset all tracking data |
| `--scheduler` | Generate cron + systemd scheduler config |
| `--metrics [PORT]` | Prometheus metrics endpoint (default 9090) |
| `--init` | Interactive setup wizard |
| `--locale {en,fr,zh}` | i18n locale |
| `--no-browser` | Don't auto-open preview.html |

---

## Input Format

`endorsers.txt` (whitespace or `|` separated):

```
Dr. Elena Vasquez | elena.vasquez@example.com | Deep Learning for Safety
Prof. Marcus Chen | marcus.chen@example.com | Deep Learning Theory
```

CSV / JSON / YAML / XLSX also supported — must contain `last_name`, `email`, `paper_title` fields.

---

## Templates

`template.txt` (plain text):

```
Dear Dr./Mr./Ms. {{ last_name }},

I trust this email finds you in good spirits and good health.

My name is {{ your_name }}, and I am an independent researcher. I have recently completed an academic paper titled "{{ your_paper_title }}" and would be honored to have your endorsement for submission to arXiv's {{ arxiv_category }} category.

...
```

Available variables: `{{ last_name }}`, `{{ email }}`, `{{ paper_title }}`, `{{ your_name }}`, `{{ your_paper_title }}`, `{{ arxiv_category }}`, `{{ signature }}`

---

## Project Structure

```
arxiv_automation/
├── run.py                    # CLI entry point (14 commands)
├── config.json               # Config with ${ENV_VAR} interpolation
├── .env                      # SMTP passwords (gitignored)
├── pyproject.toml            # Packaging, CLI entry, coverage, ruff config
├── Makefile                  # test / lint / typecheck / security / doctor / clean / all
├── .pre-commit-config.yaml   # ruff + mypy + bandit hooks
├── .github/workflows/ci.yml  # CI: lint, typecheck, test matrix, security
│
├── core/
│   ├── config.py             # Config loader + version auto-upgrade
│   ├── config_typed.py       # Frozen dataclasses (SMTPAccount, TypedConfig, ...)
│   ├── database.py           # SQLite + versioned migrations (schema v4)
│   ├── tracker.py            # Progress + AccountHealth + auto-backup
│   ├── validator.py          # Email, PDF, checksum, pre-flight
│   ├── logger.py             # Structured JSON logs + secret redaction
│   ├── ratelimiter.py        # Per-provider rate limiting
│   ├── dns_validator.py      # MX/SPF/DKIM/DMARC + parallel validation
│   ├── doctor.py             # Diagnostic checks + diagnostics.zip bundle
│   ├── lock.py               # fcntl.flock file locking
│   ├── metrics.py            # Prometheus endpoint
│   ├── notifications.py      # Discord / Slack / Desktop
│   ├── secrets.py            # Env var resolution
│   ├── credential_store.py   # Fernet-encrypted credentials
│   ├── wizard.py             # Interactive setup wizard
│   ├── email_quality.py      # Spam triggers, scoring, linting
│   ├── exceptions.py         # 8 exception classes
│   └── i18n.py               # en/fr/zh locales
│
├── parsing/
│   ├── parser.py             # TXT/CSV/JSON/YAML/XLSX + dedup
│   └── bounce.py             # SMTP response code map + bounce classification
│
├── engine/
│   ├── smtp.py               # Pooling, capability detection, latency phases
│   ├── templates.py          # Rotation, caching, validation
│   ├── orchestrator.py       # Pipeline runner
│   └── plugins.py            # Lifecycle hook system
│
├── exports/
│   └── report.py             # JSON/CSV/HTML dashboard
│
├── tests/                    # 100 tests (unit + fuzz)
├── locales/                  # en.json, fr.json, zh.json
└── plugins/                  # drop-in plugin directory
```

---

## Development

```bash
make lint        # ruff check
make typecheck   # mypy
make test        # pytest with coverage
make security    # bandit
make doctor      # system diagnostic
make all         # lint + typecheck + test + security + doctor
```

Pre-commit hooks are configured; run `pre-commit install` to enable.

### Testing

100 tests across 9 test files — unit tests for parser, validator, templates, plugins, notifications, database, exports, SMTP classification, plus **fuzz tests** that throw random malformed TXT/CSV/JSON/binary at the parser to guarantee it never crashes.

---

## Security

- Passwords only in `.env` (gitignored) or Fernet-encrypted credential store
- Secrets redacted from all logs
- Config is immutable post-validation
- Dependency scanning (pip-audit, safety) and static analysis (bandit) in CI
- See [SECURITY.md](SECURITY.md) for the vulnerability disclosure policy

---

## License

MIT — see [LICENSE](LICENSE).

---

## Roadmap

- [ ] Real-provider validation of deliverability (Gmail/Outlook bounce behavior)
- [ ] OAuth2 SMTP (XOAUTH2) for Gmail without app passwords
- [ ] Plugin API versioning + deprecation framework
- [ ] OpenTelemetry tracing with per-email event timelines
- [ ] SBOM + reproducible builds in CI
