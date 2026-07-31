import os
import time
from core.database import Database


def test_init():
    db = Database(":memory:")
    db.initialize()
    assert db.get_meta("schema_version", "0") == "4"


def test_recipient_crud():
    db = Database(":memory:")
    db.initialize()
    rid = db.upsert_recipient("Smith", "smith@test.com", "Paper A", "hash1")
    assert rid > 0
    assert db.count_recipients() == 1
    r = db.get_recipient_by_hash("hash1")
    assert r is not None
    assert r["last_name"] == "Smith"


def test_recipient_dedup():
    db = Database(":memory:")
    db.initialize()
    rid1 = db.upsert_recipient("Smith", "smith@test.com", "Paper A", "hash1")
    rid2 = db.upsert_recipient("Smith", "smith@test.com", "Paper A", "hash1")
    assert rid1 == rid2
    assert db.count_recipients() == 1


def test_account_crud():
    db = Database(":memory:")
    db.initialize()
    db.upsert_account("a@b.com", "gmail", "smtp.gmail.com", 587)
    accts = db.get_all_accounts()
    assert len(accts) == 1
    assert accts[0]["email"] == "a@b.com"


def test_rate_limits():
    db = Database(":memory:")
    db.initialize()
    db.init_rate_limit("gmail", 20, 200)
    ok, msg = db.check_rate_limit("gmail")
    assert ok is True
    db.increment_rate_limit("gmail")
    ok, msg = db.check_rate_limit("gmail")
    assert ok is True


def test_bounce():
    db = Database(":memory:")
    db.initialize()
    db.record_bounce("bad@test.com", "hard_bounce", 550, "mailbox not found")
    assert db.is_bounced("bad@test.com") is True
    assert db.is_bounced("good@test.com") is False


def test_metadata():
    db = Database(":memory:")
    db.initialize()
    db.set_meta("test_key", "test_value")
    assert db.get_meta("test_key") == "test_value"
    assert db.get_meta("nonexistent", "default") == "default"


def test_progress():
    db = Database(":memory:")
    db.initialize()
    assert db.get_progress_index() == 0
    db.set_progress_index(5)
    assert db.get_progress_index() == 5


def test_schema_migration():
    db = Database(":memory:")
    db.initialize()
    ver = db.get_meta("schema_version", "0")
    assert ver == "4"
    history = db.get_meta("migration_history", "")
    assert "1" in history
    assert "2" in history
    assert "3" in history


def test_send_record(tmp_path):
    p = str(tmp_path / "test.db")
    db = Database(p)
    db.initialize()
    rid = db.upsert_recipient("Smith", "s@t.com", "Paper", "hash")
    db.record_send(rid, "a@b.com", "success", latency_ms=100, latency_details='{"dns": 10}')
    stats = db.get_send_stats()
    assert stats["sent"] == 1


def test_account_health():
    db = Database(":memory:")
    db.initialize()
    db.upsert_account("a@b.com", "gmail", "smtp.gmail.com", 587)
    db.record_account_success("a@b.com", 100)
    healthy = db.get_healthy_accounts()
    assert len(healthy) == 1


def test_account_suspend():
    db = Database(":memory:")
    db.initialize()
    db.upsert_account("a@b.com", "gmail", "smtp.gmail.com", 587)
    db.suspend_account("a@b.com", 24)
    healthy = db.get_healthy_accounts()
    assert len(healthy) == 0


def test_get_best_account():
    db = Database(":memory:")
    db.initialize()
    db.upsert_account("a@b.com", "gmail", "smtp.gmail.com", 587)
    db.record_account_success("a@b.com", 100)
    best = db.get_best_account()
    assert best is not None
    assert best["email"] == "a@b.com"


def test_vacuum():
    db = Database(":memory:")
    db.initialize()
    db.vacuum()


def test_close():
    db = Database(":memory:")
    db.initialize()
    db.close()