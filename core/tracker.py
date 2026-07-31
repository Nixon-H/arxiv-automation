import os
import json
import time
import shutil
from datetime import datetime
from typing import Dict, Any, List, Optional, Set, Tuple

from core.logger import AppLogger


class AccountHealth:
    def __init__(self, email: str) -> None:
        self.email = email
        self.sent_today: int = 0
        self.failures_today: int = 0
        self.auth_failures: int = 0
        self.suspended_until: float = 0.0
        self.last_success: float = 0.0
        self.last_error: str = ""
        self.total_sent: int = 0

    def record_success(self) -> None:
        self.sent_today += 1
        self.total_sent += 1
        self.last_success = time.time()

    def record_failure(self, error_type: str = "unknown") -> None:
        self.failures_today += 1
        self.last_error = error_type

    def record_auth_failure(self) -> None:
        self.auth_failures += 1
        self.failures_today += 1
        self.last_error = "authentication"

    def suspend(self, hours: float = 4.0) -> None:
        self.suspended_until = time.time() + (hours * 3600.0)

    @property
    def is_suspended(self) -> bool:
        return time.time() < self.suspended_until

    def to_dict(self) -> Dict[str, Any]:
        return {
            "email": self.email,
            "sent_today": self.sent_today,
            "failures_today": self.failures_today,
            "auth_failures": self.auth_failures,
            "suspended_until": self.suspended_until,
            "last_success": self.last_success,
            "last_error": self.last_error,
            "total_sent": self.total_sent,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AccountHealth":
        h = cls(d.get("email", ""))
        h.sent_today = d.get("sent_today", 0)
        h.failures_today = d.get("failures_today", 0)
        h.auth_failures = d.get("auth_failures", 0)
        h.suspended_until = d.get("suspended_until", 0.0)
        h.last_success = d.get("last_success", 0.0)
        h.last_error = d.get("last_error", "")
        h.total_sent = d.get("total_sent", 0)
        return h


class ProgressTracker:
    def __init__(self, data_path: str = "data/progress.json") -> None:
        self.data_path = data_path
        self.backup_dir = os.path.join(os.path.dirname(data_path), "backups")
        self.current_index: int = 0
        self.last_sent_timestamp: float = 0.0
        self.current_account_idx: int = 0
        self.sent_history_hashes: Set[str] = set()
        self.queue_remaining: List[int] = []
        self.account_health: Dict[str, AccountHealth] = {}
        self.last_error: str = ""
        self.ensure_directories()
        self.load_state()

    def ensure_directories(self) -> None:
        os.makedirs(os.path.dirname(self.data_path), exist_ok=True)
        os.makedirs(self.backup_dir, exist_ok=True)

    def load_state(self) -> None:
        if not os.path.exists(self.data_path):
            self.save_state()
            return

        with open(self.data_path, "r", encoding="utf-8") as f:
            try:
                state = json.load(f)
            except (json.JSONDecodeError, Exception) as e:
                AppLogger.warn(f"Progress file corrupt, rebuilding: {e}")
                self.save_state()
                return

        self.current_index = state.get("current_index", 0)
        self.last_sent_timestamp = state.get("last_sent_timestamp", 0.0)
        self.current_account_idx = state.get("current_account_idx", 0)
        self.sent_history_hashes = set(state.get("sent_history_hashes", []))
        self.queue_remaining = state.get("queue_remaining", [])
        self.last_error = state.get("last_error", "")

        raw_health = state.get("account_health", {})
        self.account_health = {}
        for email, hdata in raw_health.items():
            self.account_health[email] = AccountHealth.from_dict(hdata)

    def _create_backup(self) -> None:
        if os.path.exists(self.data_path):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"progress_{timestamp}.json"
            backup_path = os.path.join(self.backup_dir, backup_name)
            try:
                shutil.copy2(self.data_path, backup_path)
            except Exception as e:
                AppLogger.warn(f"Backup creation failed: {e}")

    def save_state(self) -> None:
        self._create_backup()

        health_dict = {
            email: h.to_dict()
            for email, h in self.account_health.items()
        }

        state = {
            "current_index": self.current_index,
            "last_sent_timestamp": self.last_sent_timestamp,
            "current_account_idx": self.current_account_idx,
            "sent_history_hashes": list(self.sent_history_hashes),
            "queue_remaining": self.queue_remaining,
            "account_health": health_dict,
            "last_error": self.last_error,
        }

        tmp_path = self.data_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4)
        os.replace(tmp_path, self.data_path)

    def is_cooldown_active(self, cooldown_hours: float) -> Tuple[bool, float]:
        if self.last_sent_timestamp == 0.0:
            return False, 0.0
        elapsed = time.time() - self.last_sent_timestamp
        required = cooldown_hours * 3600.0
        if elapsed < required:
            remaining_hours = (required - elapsed) / 3600.0
            return True, remaining_hours
        return False, 0.0

    def register_success(self, index: int, email_hash: str, account_email: str) -> None:
        self.current_index = index + 1
        self.last_sent_timestamp = time.time()
        self.sent_history_hashes.add(email_hash)

        if account_email not in self.account_health:
            self.account_health[account_email] = AccountHealth(account_email)
        self.account_health[account_email].record_success()
        self.last_error = ""
        self.save_state()

    def register_failure(self, account_email: str, error_type: str = "unknown") -> None:
        if account_email not in self.account_health:
            self.account_health[account_email] = AccountHealth(account_email)
        self.account_health[account_email].record_failure(error_type)
        self.last_error = error_type
        self.save_state()

    def register_auth_failure(self, account_email: str) -> None:
        if account_email not in self.account_health:
            self.account_health[account_email] = AccountHealth(account_email)
        self.account_health[account_email].record_auth_failure()
        self.last_error = "authentication"
        self.save_state()

    def suspend_account(self, account_email: str, hours: float = 4.0) -> None:
        if account_email not in self.account_health:
            self.account_health[account_email] = AccountHealth(account_email)
        self.account_health[account_email].suspend(hours)
        self.save_state()

    def get_healthy_accounts(self, accounts: List[Dict[str, Any]]) -> List[int]:
        healthy: List[int] = []
        for idx, acct in enumerate(accounts):
            email = acct["email"]
            health = self.account_health.get(email)
            if health and health.is_suspended:
                AppLogger.warn(f"Account {email} is suspended until "
                               f"{datetime.fromtimestamp(health.suspended_until).strftime('%H:%M:%S')}")
                continue
            if health and health.auth_failures >= 3:
                AppLogger.warn(f"Account {email} disabled due to 3+ auth failures")
                continue
            healthy.append(idx)
        return healthy

    def reset(self) -> None:
        self.current_index = 0
        self.last_sent_timestamp = 0.0
        self.current_account_idx = 0
        self.sent_history_hashes.clear()
        self.queue_remaining.clear()
        self.account_health.clear()
        self.last_error = ""
        self.save_state()
