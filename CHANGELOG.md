# Changelog

## 5.1.0 (2026-07-30)

### Added
- In-file duplicate detection with per-category stats (email / name+paper / exact hash)
- SMTP capability detection — STARTTLS, AUTH mechanisms, SIZE, PIPELINING, 8BITMIME
- DKIM/SPF/DMARC diagnostics in `--doctor` check
- Per-phase delivery latency tracking (DNS → connect → EHLO → TLS → AUTH → DATA)
- Rich HTML dashboard with SVG pie charts and latency timeline
- SQLite migration system with schema_version and migration_history
- Dry-run preview HTML (`preview.html`, opens in browser)
- Archive sent emails as `.eml` files in `sent/`
- `--no-browser` flag for headless preview

### Changed
- pyproject.toml: proper setuptools backend, classifiers, coverage config, semantic-release
- Added LICENSE (MIT), CHANGELOG, CONTRIBUTING, SECURITY

## 5.0.0 (2026-07-29)

### Added
- Complete modular rewrite: 28 files across 5 packages
- Typed dataclass config with env var resolution
- SQLite database with 5 tables (accounts, recipients, sends, rate_limits, bounces)
- Per-provider rate limiter (gmail 20/h 200/d, outlook 15/h 150/d)
- Adaptive account selection by health score
- SMTP connection pooling with error classification (8 classes)
- Randomized exponential backoff with jitter
- Bounce processing with SMTP response code map (30+ codes)
- DNS validation: MX lookup + disposable email detection
- File locking via fcntl.flock
- Crash recovery via atomic SQLite transactions
- Structured JSON logging (.jsonl)
- Multi-template / subject / signature rotation
- Multi-format parser (TXT/CSV/JSON/YAML/XLSX auto-detect)
- Pre-flight checks and --doctor diagnostic (16 checks)
- Prometheus metrics endpoint
- Notification system (Discord / Slack / Desktop)
- Plugin architecture with before_send / after_send hooks
- i18n with en/fr/zh locales
- Scheduler generation (cron + systemd)
- Export reports (JSON / CSV / HTML)
- 11 CLI commands
