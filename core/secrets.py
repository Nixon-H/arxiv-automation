import os
import re
import subprocess
import json
from typing import Optional, Dict, Any


_ENV_REF = re.compile(r"\$\{([^}]+)\}")
_SECRET_REF = re.compile(r"secrets://([^/]+)/(.+)")


class SecretsResolver:
    def __init__(self) -> None:
        self._cache: Dict[str, str] = {}

    def resolve(self, value: str) -> str:
        if _ENV_REF.search(value):
            return self._resolve_env(value)
        if _SECRET_REF.match(value):
            return self._resolve_secret(value)
        return value

    def _resolve_env(self, value: str) -> str:
        def _repl(m: re.Match) -> str:
            key = m.group(1)
            if key in self._cache:
                return self._cache[key]
            val = os.environ.get(key)
            if val is None:
                raise ValueError(f"Environment variable '{key}' not set")
            self._cache[key] = val
            return val
        return _ENV_REF.sub(_repl, value)

    def _resolve_secret(self, value: str) -> str:
        if value.startswith("secrets://bitwarden/"):
            item_id = value.removeprefix("secrets://bitwarden/")
            return self._bitwarden(item_id)
        if value.startswith("secrets://1password/"):
            ref = value.removeprefix("secrets://1password/")
            return self._onepassword(ref)
        if value.startswith("secrets://vault/"):
            path = value.removeprefix("secrets://vault/")
            return self._hashicorp_vault(path)
        raise ValueError(f"Unknown secret provider: {value}")

    def _bitwarden(self, item_id: str) -> str:
        try:
            result = subprocess.run(
                ["bw", "get", "password", item_id],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
        raise RuntimeError(f"Bitwarden lookup failed for '{item_id}'")

    def _onepassword(self, ref: str) -> str:
        try:
            result = subprocess.run(
                ["op", "read", f"op://{ref}"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
        raise RuntimeError(f"1Password lookup failed for '{ref}'")

    def _hashicorp_vault(self, path: str) -> str:
        try:
            result = subprocess.run(
                ["vault", "kv", "get", f"-field=password", path],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
        raise RuntimeError(f"Vault lookup failed for '{path}'")

    def resolve_all(self, config: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(config, dict):
            return {k: self.resolve_all(v) for k, v in config.items()}
        if isinstance(config, list):
            return [self.resolve_all(item) for item in config]
        if isinstance(config, str):
            return self.resolve(config)
        return config
