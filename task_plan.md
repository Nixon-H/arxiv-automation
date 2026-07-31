# Task Plan: Enterprise-Grade v5.0 Upgrade

## Goal
Upgrade the modular v4.0 codebase with 20 enterprise features: SQLite persistence, typed config, rate limiting, adaptive routing, DNS validation, bounce processing, file locking, crash recovery, plugins, notifications, metrics, --doctor, i18n, testing, CI/CD, code quality, secrets mgmt, scheduler, package install.

## Build Order
- [ ] Phase 1: SQLite database layer (replaces JSON)
- [ ] Phase 2: Typed dataclass config + secrets management
- [ ] Phase 3: Rate limiter + adaptive account selection
- [ ] Phase 4: DNS validation + bounce processing
- [ ] Phase 5: File locking + crash recovery
- [ ] Phase 6: Plugin architecture + i18n
- [ ] Phase 7: Notifications + metrics
- [ ] Phase 8: --doctor health check
- [ ] Phase 9: Refactor SMTP engine (response DB, adaptive)
- [ ] Phase 10: Refactor orchestrator + tracker
- [ ] Phase 11: Testing (pytest)
- [ ] Phase 12: CI/CD + code quality + package

## Status
**Phase 1 starting** - SQLite database
