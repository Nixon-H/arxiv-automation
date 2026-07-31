# Security

## Secrets

- Passwords live in `.env` (gitignored), referenced as `${SMTP_PASSWORD_1}`
- Optional Fernet-encrypted credential store (`.credentials.enc`)
- Logger redacts secrets before writing any output

## Safety

- File locking prevents concurrent runs (single instance)
- Cooldown + per-provider rate limits (gmail 20/h, 200/d)
- Atomic SQLite transactions; crash-recovery via versioned migrations
- Automatic account suspension on auth failures
- No PDF attachments unless `attach_pdf: true`

## Auditing

```bash
bandit -r core/ engine/ parsing/ exports/ run.py
pip-audit
safety check
```

## Reporting

Security issues: open a GitHub issue with the `security` label.
