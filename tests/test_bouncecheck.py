"""Tests for engine/bouncecheck.py (pre-send deliverability checks)."""

from unittest import mock

from engine import bouncecheck


def _fake_urlopen(payload: dict):
    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            import json

            return json.dumps(payload).encode()

    return mock.patch("engine.bouncecheck.urllib.request.urlopen", return_value=FakeResp())


def test_get_mx_sorted():
    with _fake_urlopen(
        {
            "Answer": [
                {"type": 15, "data": "10 mx2.example.com."},
                {"type": 15, "data": "5 mx1.example.com."},
            ]
        }
    ):
        mxs = bouncecheck.get_mx("example.com")
    assert mxs == ["mx1.example.com", "mx2.example.com"]


def test_get_mx_empty():
    with _fake_urlopen({"Answer": []}):
        assert bouncecheck.get_mx("example.com") == []


def test_new_identity_success():
    with mock.patch("engine.bouncecheck.socket.create_connection") as cc:
        cc.return_value.sendall = mock.MagicMock()
        cc.return_value.close = mock.MagicMock()
        assert bouncecheck.new_identity() is True


def test_new_identity_failure():
    with mock.patch("engine.bouncecheck.socket.create_connection", side_effect=OSError):
        assert bouncecheck.new_identity() is False


def test_tor_is_up_true():
    with mock.patch("engine.bouncecheck.socket.socket") as sock:
        assert bouncecheck.tor_is_up() is True


def test_tor_is_up_false():
    with mock.patch("engine.bouncecheck.socket.socket") as sock:
        sock.return_value.connect.side_effect = OSError
        assert bouncecheck.tor_is_up() is False


def test_random_local_valid_chars():
    for _ in range(10):
        assert len(bouncecheck._random_local()) == 12


def test_check_email_tor_unavailable():
    with mock.patch("engine.bouncecheck.bootstrap_tor", return_value=False):
        assert bouncecheck.check_email("a@example.com")[0] == "UNKNOWN"


def test_check_email_no_mx():
    with (
        mock.patch("engine.bouncecheck.bootstrap_tor", return_value=True),
        mock.patch("engine.bouncecheck.get_mx", return_value=[]),
    ):
        verdict, detail = bouncecheck.check_email("a@example.com")
        assert verdict == "INVALID"
        assert "MX" in detail


def test_check_email_valid():
    with (
        mock.patch("engine.bouncecheck.bootstrap_tor", return_value=True),
        mock.patch("engine.bouncecheck.get_mx", return_value=["mx1.example.com"]),
        mock.patch(
            "engine.bouncecheck._rcpt",
            side_effect=[(550, "5.1.1 does not exist"), (250, "ok")],
        ),
    ):
        verdict, detail = bouncecheck.check_email("user@example.com")
        assert verdict == "VALID"


def test_check_email_catchall():
    with (
        mock.patch("engine.bouncecheck.bootstrap_tor", return_value=True),
        mock.patch("engine.bouncecheck.get_mx", return_value=["mx1.example.com"]),
        mock.patch("engine.bouncecheck._rcpt", return_value=(250, "ok")),
    ):
        verdict, _ = bouncecheck.check_email("user@example.com")
        assert verdict == "CATCHALL"


def test_check_email_invalid_mailbox():
    with (
        mock.patch("engine.bouncecheck.bootstrap_tor", return_value=True),
        mock.patch("engine.bouncecheck.get_mx", return_value=["mx1.example.com"]),
        mock.patch(
            "engine.bouncecheck._rcpt",
            side_effect=[(550, "5.1.1 does not exist"), (550, "5.1.1 does not exist")],
        ),
    ):
        verdict, _ = bouncecheck.check_email("dead@example.com")
        assert verdict == "INVALID"


def test_check_email_spamhaus_unknown_rotates():
    with (
        mock.patch("engine.bouncecheck.bootstrap_tor", return_value=True),
        mock.patch("engine.bouncecheck.get_mx", return_value=["mx1.example.com"]),
        mock.patch("engine.bouncecheck._rcpt", return_value=(550, "5.7.1 blocked using Spamhaus")),
        mock.patch("engine.bouncecheck.new_identity", return_value=True) as ni,
    ):
        verdict, _ = bouncecheck.check_email("user@example.com", max_rotations=2)
        assert verdict == "UNKNOWN"
        assert ni.call_count == 2


def test_check_email_auth_required_unknown():
    with (
        mock.patch("engine.bouncecheck.bootstrap_tor", return_value=True),
        mock.patch("engine.bouncecheck.get_mx", return_value=["mx1.example.com"]),
        mock.patch(
            "engine.bouncecheck._rcpt", return_value=(553, "authentication is required")
        ),
    ):
        verdict, _ = bouncecheck.check_email("user@example.com", max_rotations=1)
        assert verdict == "UNKNOWN"


def test_check_email_protocol_error_unknown():
    with (
        mock.patch("engine.bouncecheck.bootstrap_tor", return_value=True),
        mock.patch("engine.bouncecheck.get_mx", return_value=["mx1.example.com"]),
        mock.patch(
            "engine.bouncecheck._rcpt", return_value=(503, "bad sequence of commands")
        ),
    ):
        verdict, _ = bouncecheck.check_email("user@example.com", max_rotations=1)
        assert verdict == "UNKNOWN"


def test_check_email_no_rcpt_unknown():
    with (
        mock.patch("engine.bouncecheck.bootstrap_tor", return_value=True),
        mock.patch("engine.bouncecheck.get_mx", return_value=["mx1.example.com"]),
        mock.patch("engine.bouncecheck._rcpt", return_value=None),
    ):
        verdict, _ = bouncecheck.check_email("user@example.com", max_rotations=1)
        assert verdict == "UNKNOWN"


def test_rcpt_restores_socket():
    original = bouncecheck._ORIGINAL_CREATE
    with mock.patch("engine.bouncecheck.socket.create_connection") as cc:
        cc.side_effect = OSError("no route")
        result = bouncecheck._rcpt("mx.example.com", "s@example.com", "r@example.com")
    assert result is None
    assert bouncecheck.socket.create_connection is original
