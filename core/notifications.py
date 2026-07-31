import json
import platform
import subprocess
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from core.logger import AppLogger


class Notifier:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    def send_discord(self, webhook_url: str, message: str) -> bool:
        try:
            payload = json.dumps({"content": message}).encode()
            req = Request(webhook_url, data=payload, method="POST")
            req.add_header("Content-Type", "application/json")
            with urlopen(req, timeout=10) as resp:
                return resp.status == 204
        except (URLError, Exception) as e:
            AppLogger.warn(f"Discord notification failed: {e}")
            return False

    def send_slack(self, webhook_url: str, message: str) -> bool:
        try:
            payload = json.dumps({"text": message}).encode()
            req = Request(webhook_url, data=payload, method="POST")
            req.add_header("Content-Type", "application/json")
            with urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except (URLError, Exception) as e:
            AppLogger.warn(f"Slack notification failed: {e}")
            return False

    def send_desktop(self, title: str, message: str) -> bool:
        system = platform.system().lower()
        try:
            if system == "linux":
                subprocess.run(
                    ["notify-send", title, message],
                    timeout=5, capture_output=True,
                )
                return True
            elif system == "darwin":
                subprocess.run(
                    ["osascript", "-e",
                     f'display notification "{message}" with title "{title}"'],
                    timeout=5, capture_output=True,
                )
                return True
            elif system == "windows":
                try:
                    from win10toast import ToastNotifier
                    n = ToastNotifier()
                    n.show_toast(title, message, duration=5)
                    return True
                except ImportError:
                    AppLogger.warn("win10toast not installed for desktop notification")
                    return False
            return False
        except Exception as e:
            AppLogger.warn(f"Desktop notification failed: {e}")
            return False

    def notify_all(self, title: str, message: str) -> None:
        self.send_desktop(title, message)

        if "discord" in self.config:
            self.send_discord(self.config["discord"], message)

        if "slack" in self.config:
            self.send_slack(self.config["slack"], message)

    def send_completion(self, stats: dict[str, Any]) -> None:
        msg = (
            f"📬 Dispatch Complete\n"
            f"Sent: {stats.get('sent', 0)} | Failed: {stats.get('failed', 0)} | "
            f"Skipped: {stats.get('skipped', 0)} | Duplicates: {stats.get('duplicates', 0)}"
        )
        self.notify_all("arXiv Dispatch Complete", msg)
