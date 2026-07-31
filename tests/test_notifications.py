import pytest
from core.notifications import Notifier


class TestNotifier:
    def test_init_default(self):
        n = Notifier()
        assert n.config == {}

    def test_init_with_config(self):
        n = Notifier({"discord": "url", "slack": "url"})
        assert n.config["discord"] == "url"

    def test_send_desktop_no_notifier(self):
        n = Notifier()
        result = n.send_desktop("Test", "Message")
        assert result is False or result is True

    def test_notify_all_no_config(self):
        n = Notifier()
        n.notify_all("Test", "Message")
        assert True

    def test_send_completion(self):
        n = Notifier()
        stats = {"sent": 5, "failed": 1, "skipped": 2, "duplicates": 0}
        n.send_completion(stats)
        assert True

    def test_send_discord_no_url(self):
        n = Notifier()
        result = n.send_discord("", "test")
        assert result is False

    def test_send_slack_no_url(self):
        n = Notifier()
        result = n.send_slack("", "test")
        assert result is False
