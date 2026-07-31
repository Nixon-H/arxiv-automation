# Contributing

## Architecture

```
arxiv_automation/
├── core/           Configuration, database, logging, validation, DNS, metrics, i18n
├── engine/         SMTP, templates, orchestration, plugins
├── parsing/        Multi-format endorser parser (TXT/CSV/JSON/YAML/XLSX), bounce analysis
├── exports/        Report generation (HTML/JSON/CSV)
└── run.py          CLI entry point
```

### Dispatch Flow

```
run.py --live
  │
  ├──► FileLock (fcntl)          — prevent concurrent runs
  ├──► DataParser                 — parse endorsers.txt → records[]
  ├──► Database.upsert_recipient  — register recipients
  ├──► RateLimiter.check          — per-provider rate limit
  ├──► SmtpEngine                 — send via SMTP pool
  │     ├── SmtpConnectionPool    — EHLO/STARTTLS/EHLO → detect capabilities
  │     ├── SmtpEngine.send_atomic — build MIME → sendmail
  │     └── BounceClassifier      — classify SMTP response codes
  ├──► Database.record_send       — persist result
  ├──► PluginManager              — run lifecycle hooks
  │     ├── before_validate       ┐
  │     ├── before_send           │
  │     ├── after_send            ├── user-defined plugins/
  │     ├── after_failure         │
  │     ├── after_retry           │
  │     └── before_archive        ┘
  └──► archive_sent_email()       — save .eml to sent/
```

### Session Sequence

```
CLI ──► OrchestrationRunner
           │
           ├── dry_run  → render templates → print → generate preview.html
           │
           ├── test     → send to test address via first account
           │
           └── live     → loop: validate → quality check → render →
                          before_send hook → SMTP send → after_send hook →
                          archive .eml → delay → next
```

## Development Setup

```bash
git clone https://github.com/anomalyco/arxiv-automation.git
cd arxiv-automation
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,all]"
```

## Code Style

- Python 3.10+ type annotations on all signatures
- Snake_case for functions and variables
- Follow existing patterns in the module you're editing

Run linting before submitting:

```bash
ruff check .
```

## Testing

```bash
pytest tests/ -v --cov
```

## Pull Requests

1. Branch from `main`
2. Add tests for new functionality
3. Update CHANGELOG.md
4. Run `python run.py --validate-config` to verify config
5. Run `python run.py --doctor` to verify system health

## Security

See SECURITY.md for reporting vulnerabilities.
