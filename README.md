# arXiv Endorsement Dispatch Engine

Automated, production-grade email dispatch system for requesting arXiv endorsements. Handles the full pipeline — parsing endorser lists, rendering personalized emails, routing through multiple SMTP accounts with health-aware load balancing, retries, bounce handling, and deliverability analytics.

**Author:** Nixon-H

> **Disclaimer:** Use responsibly. Respect recipient privacy, follow anti-spam regulations (CAN-SPAM, GDPR), and only contact researchers who have indicated openness to endorsement requests.

---

## Features

### Core Dispatch
- **Multi-account routing** — health-aware adaptive selection (success rate + latency weighted)
- **Per-provider rate limiting** — gmail 20/h 200/d, outlook 15/h 150/d (configurable)
- **SMTP connection pooling** with capability detection (STARTTLS, AUTH mechanisms, SIZE, PIPELINING, 8BITMIME)
- **Error classification** — 8-class taxonomy with recovery strategies (hard/soft bounce, auth, rate-limit, timeout, TLS, DNS, permanent)
- **Randomized exponential backoff** with jitter between retries
- **Bounce processing** — 30+ SMTP response codes mapped to actions + text-pattern fallback
- **DNS validation** — MX lookup, A-record fallback, disposable email detection

### Data & Parsing
- Multi-format input: **TXT / CSV / JSON / YAML / XLSX** with auto-detection
- Unicode normalization (NFKC)
- In-file duplicate detection with per-category stats:
  - Duplicate email
  - Duplicate (name + paper)
  - Duplicate exact record hash
- PDF header validation for your manuscript

### Template Engine
- Multi-template rotation with **HTML + plain-text** pair support
- Subject-line rotation (anti-spam-signature diversity)
- Signature profiles
- Auto plain-text generation from HTML (`strip_html`)
- Template linting (greeting, line length, spacing) and HTML tag-balance sanity checks
- Render validation (all `{{ var }}` placeholders resolved)
- **Email quality scoring** — 0–100 composite score with A–F grade (spam triggers, broken links, lint issues)

### Safety & Reliability
- **File locking** (`fcntl.flock`) — prevents concurrent runs
- **Atomic SQLite transactions** — crash recovery without corruption
- **Versioned schema migrations** — `schema_version` + `migration_history`
- Auto-backup of progress state
- Secret redaction in all logs (7 regex patterns + env var detection)
- **Frozen (immutable) config** after validation
- Pre-flight checks + `--doctor` diagnostic (16+ checks incl. DKIM/SPF/DMARC, cert info, template diff)

### Observability
- Structured JSON logging (`.jsonl`) with **correlation IDs** per send
- Per-phase delivery latency: DNS → connect → EHLO → TLS → AUTH → DATA → TOTAL
- Prometheus metrics endpoint (`/metrics`)
- Rich HTML dashboard with SVG pie charts and latency timeline
- Export reports: JSON / CSV / HTML
- Diagnostic bundle: `--doctor=bundle` → `diagnostics.zip`

### Extensibility
- **Plugin architecture** — lifecycle hooks:
  - `@hook_before_send` / `@hook_after_send`
  - `@hook_before_validate` / `@hook_after_failure`
  - `@hook_after_retry` / `@hook_before_archive`
- i18n (en / fr / zh)
- Notifications: Discord / Slack / Desktop

---

## Installation

```bash
git clone <your-repo-url>
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
Dr.  Elena Vasquez|  elena.vasquez@example.com| Deep Learning for Safety
Prof.  Daniel Okafor|  daniel.okafor@example.com| Deep Learning
```

CSV / JSON / YAML / XLSX also supported — must contain `last_name`, `email`, `paper_title` fields.

---

## Templates

`template.txt` (plain text):

```
Dear Dr./Mr./Ms. {{ last_name }},

I hope this email finds you well.

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

- Real-provider validation of deliverability (Gmail/Outlook bounce behavior)
- OAuth2 SMTP (XOAUTH2) for Gmail without app passwords
- Plugin API versioning + deprecation framework
- OpenTelemetry tracing with per-email event timelines
- SBOM + reproducible builds in CI
