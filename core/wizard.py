import json
import os
from typing import Any

from core.logger import AppLogger

_CONFIG_PATH = "config.json"
_ENV_PATH = ".env"
_TEMPLATE = """# arXiv Endorsement Dispatch — Configuration
# Edit this file or run `arxiv-mail init` to regenerate.

{
  "config_version": 2,
  "sender_identity": {
    "your_name": "",
    "your_paper_title": "",
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
      "email": "",
      "password": "${SMTP_PASSWORD_1}",
      "server": "smtp.gmail.com",
      "port": 587,
      "display_name": "Research Outreach",
      "max_per_hour": 20,
      "max_per_day": 200
    }
  ]
}
"""

_PROFILES: dict[str, dict[str, Any]] = {
    "gmail": {"server": "smtp.gmail.com", "port": 587, "max_per_hour": 20, "max_per_day": 200},
    "outlook": {"server": "smtp.office365.com", "port": 587, "max_per_hour": 15, "max_per_day": 150},
    "yahoo": {"server": "smtp.mail.yahoo.com", "port": 587, "max_per_hour": 10, "max_per_day": 100},
    "custom": {"server": "", "port": 587, "max_per_hour": 20, "max_per_day": 200},
}


def _ask(prompt: str, default: str = "") -> str:
    if default:
        val = input(f"  {prompt} [{default}]: ").strip()
        return val if val else default
    val = input(f"  {prompt}: ").strip()
    return val


def _ask_yesno(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    val = input(f"  {prompt} ({hint}): ").strip().lower()
    if not val:
        return default
    return val.startswith("y")


def run_wizard() -> int:
    print()
    print("=" * 50)
    print("  arXiv Endorsement Dispatch — Setup Wizard")
    print("=" * 50)
    print()
    print("This will create config.json and .env for you.")
    print()

    your_name = _ask("Your full name")
    your_paper = _ask("Your paper title")
    arxiv_cat = _ask("arXiv category", "cs.AI")

    config: dict[str, Any] = {
        "config_version": 2,
        "sender_identity": {
            "your_name": your_name,
            "your_paper_title": your_paper,
            "arxiv_category": arxiv_cat,
        },
        "safety_and_limits": {
            "cooldown_hours": 24.0,
            "smtp_timeout_seconds": 30.0,
            "random_delay_range_seconds": [5, 15],
            "max_retries": 3,
            "initial_backoff_seconds": 2.0,
        },
        "accounts": [],
    }

    while True:
        print()
        print("--- Account #{} ---".format(len(config["accounts"]) + 1))
        print("  Available profiles: gmail, outlook, yahoo, custom")
        provider = _ask("Provider", "gmail").lower()
        profile = _PROFILES.get(provider, _PROFILES["custom"])

        email = _ask("Email address")
        pw_name = f"SMTP_PASSWORD_{len(config['accounts']) + 1}"
        pw = _ask(f"Password (stored as ${pw_name} in .env)")

        display = _ask("Display name", your_name or "Research Outreach")
        server = _ask("SMTP server", profile["server"])
        port_str = _ask("SMTP port", str(profile["port"]))

        config["accounts"].append({
            "provider": provider,
            "email": email,
            "password": "${" + pw_name + "}",
            "server": server,
            "port": int(port_str),
            "display_name": display,
            "max_per_hour": profile["max_per_hour"],
            "max_per_day": profile["max_per_day"],
        })

        if not _ask_yesno("Add another account?", False):
            break

    # Write .env
    env_lines: list[str] = []
    if os.path.exists(_ENV_PATH):
        with open(_ENV_PATH) as f:
            env_lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    existing: dict[str, str] = {}
    for line in env_lines:
        if "=" in line:
            k, v = line.split("=", 1)
            existing[k.strip()] = v.strip()

    for i, acct in enumerate(config["accounts"]):
        pw_key = f"SMTP_PASSWORD_{i + 1}"
        existing[pw_key] = pw if not existing.get(pw_key) else existing[pw_key]

    with open(_ENV_PATH, "w") as f:
        f.write("# arXiv Automation — Secrets\n")
        f.write("# Created by arxiv-mail init\n\n")
        for k, v in existing.items():
            f.write(f"{k}={v}\n")

    with open(_CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    print()
    AppLogger.success(f"Written {_CONFIG_PATH} and {_ENV_PATH}")
    print()
    print("  Next steps:")
    print("    1. Edit .env if passwords need updating")
    print("    2. Run: python run.py --validate-config")
    print("    3. Run: python run.py --dry-run")
    print("    4. To send: python run.py --live --send N")
    print()
    return 0
