# Templates

- `template.txt` — plain-text body; `{{ var }}` placeholders
- `template.html` — HTML body (mirrors txt)
- `template_followup.txt` — follow-up email body
- `preview.html` — generated dry-run preview dashboard

## Placeholders

| Var | Source |
|-----|--------|
| `{{ greeting }}` | `{title} {last_name}` (e.g. "Dr. Smith") |
| `{{ title }}` | detected honorific |
| `{{ last_name }}` | recipient surname |
| `{{ paper_title }}` | recipient's paper |
| `{{ your_name }}` | from config `sender_identity` |
| `{{ your_paper_title }}` | from config |
| `{{ arxiv_category }}` | from config |
