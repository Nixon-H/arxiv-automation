from dataclasses import dataclass, field
from typing import List, Tuple, Optional
import json
import os
import re


_ENV_REF = re.compile(r"\$\{([^}]+)\}")


@dataclass(frozen=True)
class SMTPAccount:
    provider: str
    email: str
    password: str
    server: str
    port: int = 587
    display_name: str = "Research Outreach"
    max_per_hour: int = 20
    max_per_day: int = 200

    @property
    def is_gmail(self) -> bool:
        return "gmail" in self.provider.lower()

    @property
    def is_outlook(self) -> bool:
        return "outlook" in self.provider.lower() or "office" in self.provider.lower()


@dataclass(frozen=True)
class SenderIdentity:
    your_name: str = ""
    your_paper_title: str = ""
    arxiv_category: str = "cs.AI"


@dataclass(frozen=True)
class SafetyLimits:
    cooldown_hours: float = 24.0
    smtp_timeout_seconds: float = 30.0
    random_delay_range_seconds: Tuple[float, float] = (5.0, 15.0)
    max_retries: int = 3
    initial_backoff_seconds: float = 2.0
    attach_pdf: bool = False


@dataclass(frozen=True)
class TypedConfig:
    sender_identity: SenderIdentity = field(default_factory=SenderIdentity)
    safety_limits: SafetyLimits = field(default_factory=SafetyLimits)
    accounts: List[SMTPAccount] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "TypedConfig":
        identity_data = data.get("sender_identity", {})
        limits_data = data.get("safety_and_limits", {})
        accounts_data: list = []

        raw_accounts = data.get("accounts", [])
        raw_profiles = data.get("profiles", {})

        accounts_data.extend(raw_accounts)

        if raw_profiles:
            for name, prof in raw_profiles.items():
                accts = prof.get("accounts", [])
                for a in accts:
                    if "provider" not in a:
                        a["provider"] = name
                    accounts_data.append(a)

        delay = limits_data.get("random_delay_range_seconds", [5, 15])
        if isinstance(delay, list) and len(delay) == 2:
            delay_tuple = (float(delay[0]), float(delay[1]))
        else:
            delay_tuple = (5.0, 15.0)

        return cls(
            sender_identity=SenderIdentity(
                your_name=identity_data.get("your_name", ""),
                your_paper_title=identity_data.get("your_paper_title", ""),
                arxiv_category=identity_data.get("arxiv_category", "cs.AI"),
            ),
            safety_limits=SafetyLimits(
                cooldown_hours=float(limits_data.get("cooldown_hours", 24.0)),
                smtp_timeout_seconds=float(limits_data.get("smtp_timeout_seconds", 30.0)),
                random_delay_range_seconds=delay_tuple,
                max_retries=int(limits_data.get("max_retries", 3)),
                initial_backoff_seconds=float(limits_data.get("initial_backoff_seconds", 2.0)),
                attach_pdf=bool(limits_data.get("attach_pdf", False)),
            ),
            accounts=[
                SMTPAccount(
                    provider=a.get("provider", "unknown"),
                    email=a["email"],
                    password=a["password"],
                    server=a["server"],
                    port=int(a.get("port", 587)),
                    display_name=a.get("display_name", "Research Outreach"),
                    max_per_hour=int(a.get("max_per_hour", 20)),
                    max_per_day=int(a.get("max_per_day", 200)),
                )
                for a in accounts_data
            ],
        )

    @classmethod
    def from_json(cls, path: str) -> "TypedConfig":
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return cls.from_dict(_resolve_env_vars(raw))

    def get_provider_limits(self, provider: str) -> Tuple[int, int]:
        for acct in self.accounts:
            if acct.provider == provider:
                return acct.max_per_hour, acct.max_per_day
        return 20, 200


def _resolve_env_vars(obj):
    if isinstance(obj, str):
        if "${" in obj:
            def repl(m: re.Match) -> str:
                return os.environ.get(m.group(1), "")
            return _ENV_REF.sub(repl, obj)
        return obj
    if isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_vars(item) for item in obj]
    return obj
