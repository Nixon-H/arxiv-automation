# Quick Start

## 1. Install

```bash
pip install -e .
```

## 2. Configure

```bash
python run.py --init          # interactive wizard
# or edit config.json + .env manually
```

Gmail users: enable 2-Step Verification, create an [App Password](https://myaccount.google.com/apppasswords),
and put it in `.env`:

```env
SMTP_PASSWORD_1=your_app_password
```

Reference it in `config.json`:

```json
{
  "accounts": [
    {
      "email": "you@gmail.com",
      "password": "${SMTP_PASSWORD_1}",
      "server": "smtp.gmail.com",
      "port": 587
    }
  ]
}
```

## 3. Prepare input

`endorsers.txt`:

```
Dr. Alice Smith is qualified to endorse.
My Paper Title
alice.smith@example.com
```

## 4. Verify

```bash
python run.py --verify              # pre-flight checks
python run.py --test you@gmail.com  # send a test email to yourself
python run.py --dry-run             # preview next send + open preview.html
```

## 5. Send

```bash
python run.py --live                # send one email
python run.py --live --send 5       # send a batch of 5
```

## 6. Follow up

```bash
python run.py --followups 7         # who hasn't replied in 7 days?
python run.py --followups 7 --send 3
python run.py --mark-replied alice.smith@example.com
```

!!! warning
    The tool will **never** send anything unless you run `--live` or `--followups --send`.
    `--dry-run` is fully offline.
