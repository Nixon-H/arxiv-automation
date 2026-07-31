# arXiv Automation

Automated arXiv endorsement outreach engine — production-grade, modular, and safe by default.

![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![tests](https://img.shields.io/badge/tests-103%20passing-brightgreen)

## What it does

Reaches out to qualified arXiv endorsers on your behalf — parsing endorser lists, rendering
personalized emails, sending via Gmail/Outlook SMTP with adaptive rate limiting, tracking every
send in SQLite, and following up with non-responders — all while keeping your account healthy.

## Highlights

- **Safe by default**: dry-run previews, cooldowns, rate limits, file locking, resumable batches
- **Deliverability-first**: SPF/DKIM/DMARC diagnostics, anti-spam scoring, bounce classification
- **Observable**: structured JSON logs, per-phase latency, Prometheus metrics, HTML dashboards
- **Extensible**: 6 plugin hooks, i18n, encrypted credential store, follow-up tracking
- **Tested**: 103 unit + fuzz tests, CI on 3 OSes × 3 Python versions

## Documentation

- [Quick Start](quickstart.md)
- [CLI Reference](cli.md)
- [Configuration](configuration.md)
- [API Reference](api/core/config.md)
- [Development](development.md)

## License

MIT
