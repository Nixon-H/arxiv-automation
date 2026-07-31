# Configuration

`config.json` is the single source of truth. Secrets use `${ENV_VAR}` interpolation from `.env`.

```json
{
  "config_version": 2,
  "sender_identity": {
    "your_name": "Nixon-H",
    "your_paper_title": "My Paper Title",
    "arxiv_category": "cs.AI"
  },
  "safety_and_limits": {
    "cooldown_hours": 24,
    "random_delay_range_seconds": [5, 15],
    "smtp_timeout_seconds": 30,
    "attach_pdf": false
  },
  "accounts": [
    {
      "email": "you@gmail.com",
      "password": "${SMTP_PASSWORD_1}",
      "server": "smtp.gmail.com",
      "port": 587,
      "display_name": "Research Outreach",
      "max_per_hour": 20,
      "max_per_day": 200
    }
  ]
}
```

## Sections

- **sender_identity**: your name, paper title, arXiv category — interpolated into templates
- **safety_and_limits**: cooldown, delay range, SMTP timeout, PDF attachment toggle
- **accounts**: one or more SMTP profiles; adaptive routing picks the healthiest
- **notifications** (optional): `{"discord": url, "slack": url}`

## Versioning

`config_version: 2` auto-upgrades older configs on load.
