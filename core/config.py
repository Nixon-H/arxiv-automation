import os
import json
from typing import Any, Dict, List

from core.exceptions import ConfigValidationError
from core.config_typed import TypedConfig, _resolve_env_vars
from core.secrets import SecretsResolver
from core.logger import AppLogger


CONFIG_VERSION = 2


class AppConfig:
    def __init__(self, config_path: str = "config.json", dotenv_path: str = ".env") -> None:
        self.config_path = config_path
        self.dotenv_path = dotenv_path
        self._typed: TypedConfig
        self._raw: Dict[str, Any] = {}
        self._load_dotenv()
        self.load_and_validate()

    def _load_dotenv(self) -> None:
        if os.path.exists(self.dotenv_path):
            with open(self.dotenv_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip("\"'")
                    if key and not os.environ.get(key):
                        os.environ.setdefault(key, val)

    def load_and_validate(self) -> None:
        if not os.path.exists(self.config_path):
            raise ConfigValidationError(f"Config file '{self.config_path}' not found.")

        with open(self.config_path, "r", encoding="utf-8") as f:
            try:
                raw = json.load(f)
            except json.JSONDecodeError as e:
                raise ConfigValidationError(f"Malformed JSON: {e}")

        resolver = SecretsResolver()
        self._raw = resolver.resolve_all(raw)

        self._raw = _resolve_env_vars(self._raw)

        req_keys = ["sender_identity", "safety_and_limits", "accounts"]
        for rk in req_keys:
            if rk not in self._raw:
                raise ConfigValidationError(f"Missing config section: '{rk}'")

        accounts = self._raw.get("accounts", [])
        if not isinstance(accounts, list) or not accounts:
            raise ConfigValidationError("At least one SMTP account required.")

        for idx, acct in enumerate(accounts):
            for field in ("provider", "email", "password", "server", "port"):
                if field not in acct:
                    raise ConfigValidationError(f"Account #{idx} missing '{field}'")

        notif_config = self._raw.get("notifications", {})
        self._raw["notifications"] = notif_config

        ver = self._raw.get("config_version", 1)
        if ver < CONFIG_VERSION:
            self._raw["config_version"] = CONFIG_VERSION
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self._raw, f, indent=4)
            AppLogger.info(f"Config upgraded from v{ver} to v{CONFIG_VERSION}")

        self._typed = TypedConfig.from_dict(self._raw)

    @property
    def typed(self) -> TypedConfig:
        return self._typed

    @property
    def sender_identity(self) -> dict:
        si = self._typed.sender_identity
        return {"your_name": si.your_name, "your_paper_title": si.your_paper_title, "arxiv_category": si.arxiv_category}

    @property
    def safety_limits(self) -> dict:
        sl = self._typed.safety_limits
        return {
            "cooldown_hours": sl.cooldown_hours,
            "smtp_timeout_seconds": sl.smtp_timeout_seconds,
            "random_delay_range_seconds": list(sl.random_delay_range_seconds),
            "max_retries": sl.max_retries,
            "initial_backoff_seconds": sl.initial_backoff_seconds,
            "attach_pdf": sl.attach_pdf,
        }

    @property
    def accounts(self) -> List[dict]:
        return [
            {
                "provider": a.provider,
                "email": a.email,
                "password": a.password,
                "server": a.server,
                "port": a.port,
                "display_name": a.display_name,
                "max_per_hour": a.max_per_hour,
                "max_per_day": a.max_per_day,
            }
            for a in self._typed.accounts
        ]

    @property
    def notifications(self) -> dict:
        return self._raw.get("notifications", {})

    def get(self, key: str, default: Any = None) -> Any:
        return self._raw.get(key, default)
