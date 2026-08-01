import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any

DB_PATH = "data/automation.db"


def get_db_path() -> str:
    return os.environ.get("ARXIV_DB_PATH", DB_PATH)


class Database:
    _instances: dict[str, "Database"] = {}
    _lock = threading.Lock()

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._local = threading.local()

    @classmethod
    def get_instance(cls, db_path: str | None = None) -> "Database":
        path = db_path or get_db_path()
        with cls._lock:
            if path not in cls._instances:
                dirname = os.path.dirname(path)
                if dirname:
                    os.makedirs(dirname, exist_ok=True)
                cls._instances[path] = cls(path)
                cls._instances[path].initialize()
            return cls._instances[path]

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
        return self._local.conn

    @contextmanager
    def transaction(self):
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        cur = self._get_conn().execute(sql, params)
        self._get_conn().commit()
        return cur

    def executemany(self, sql: str, params: list[tuple]) -> sqlite3.Cursor:
        cur = self._get_conn().executemany(sql, params)
        self._get_conn().commit()
        return cur

    def fetchone(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        row = self._get_conn().execute(sql, params).fetchone()
        if row:
            return dict(row)
        return None

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self._get_conn().execute(sql, params).fetchall()]

    def initialize(self) -> None:
        with self.transaction() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS recipients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    last_name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    paper_title TEXT NOT NULL,
                    email_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(email_hash)
                );

                CREATE TABLE IF NOT EXISTS sends (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recipient_id INTEGER NOT NULL,
                    account_email TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_type TEXT DEFAULT '',
                    error_detail TEXT DEFAULT '',
                    latency_ms INTEGER DEFAULT 0,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(recipient_id) REFERENCES recipients(id)
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    email TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    server TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    sent_today INTEGER DEFAULT 0,
                    sent_total INTEGER DEFAULT 0,
                    failures_today INTEGER DEFAULT 0,
                    auth_failures INTEGER DEFAULT 0,
                    suspended_until REAL DEFAULT 0,
                    last_error TEXT DEFAULT '',
                    last_success REAL DEFAULT 0,
                    avg_latency_ms REAL DEFAULT 0,
                    success_rate REAL DEFAULT 1.0,
                    daily_reset_date TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS rate_limits (
                    provider TEXT PRIMARY KEY,
                    max_per_hour INTEGER DEFAULT 20,
                    max_per_day INTEGER DEFAULT 200,
                    sent_this_hour INTEGER DEFAULT 0,
                    sent_this_day INTEGER DEFAULT 0,
                    hour_window_start TEXT,
                    day_window_start TEXT
                );

                CREATE TABLE IF NOT EXISTS bounces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recipient_email TEXT NOT NULL,
                    bounce_type TEXT NOT NULL,
                    smtp_code INTEGER DEFAULT 0,
                    diagnostic TEXT DEFAULT '',
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS email_checks (
                    email TEXT PRIMARY KEY,
                    verdict TEXT NOT NULL,
                    mx_host TEXT DEFAULT '',
                    detail TEXT DEFAULT '',
                    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_sends_recipient ON sends(recipient_id);
                CREATE INDEX IF NOT EXISTS idx_sends_status ON sends(status);
                CREATE INDEX IF NOT EXISTS idx_sends_timestamp ON sends(timestamp);
                CREATE INDEX IF NOT EXISTS idx_bounces_email ON bounces(recipient_email);
            """)
        self._migrate()

    SCHEMA_VERSION = 6

    MIGRATIONS: dict[int, list[str]] = {
        1: [
            "ALTER TABLE sends ADD COLUMN latency_details TEXT DEFAULT ''",
        ],
        2: [
            "ALTER TABLE sends ADD COLUMN body_fingerprint TEXT DEFAULT ''",
            "ALTER TABLE sends ADD COLUMN smtp_conversation TEXT DEFAULT ''",
        ],
        3: [
            "ALTER TABLE sends ADD COLUMN correlation_id TEXT DEFAULT ''",
        ],
        4: [
            "ALTER TABLE recipients ADD COLUMN last_replied_at TEXT DEFAULT ''",
            "ALTER TABLE recipients ADD COLUMN followup_count INTEGER DEFAULT 0",
        ],
        5: [
            """CREATE TABLE IF NOT EXISTS email_checks (
                email TEXT PRIMARY KEY,
                verdict TEXT NOT NULL,
                mx_host TEXT DEFAULT '',
                detail TEXT DEFAULT '',
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
        ],
    }

    def _migrate(self) -> None:
        current = int(self.get_meta("schema_version", "0"))
        if current >= self.SCHEMA_VERSION:
            return
        applied = self.get_meta("migration_history", "").split(",") if self.get_meta("migration_history", "") else []
        for ver in range(current + 1, self.SCHEMA_VERSION + 1):
            statements = self.MIGRATIONS.get(ver, [])
            for sql in statements:
                try:
                    self.execute(sql)
                except sqlite3.OperationalError:
                    pass
            if statements:
                applied.append(str(ver))
            self.set_meta("schema_version", str(ver))
            self.set_meta("migration_history", ",".join(applied))

    # --- Recipients ---

    def upsert_recipient(self, last_name: str, email: str, paper_title: str, email_hash: str) -> int:
        existing = self.fetchone(
            "SELECT id FROM recipients WHERE email_hash = ?", (email_hash,)
        )
        if existing:
            return existing["id"]
        self.execute(
            "INSERT INTO recipients (last_name, email, paper_title, email_hash) VALUES (?, ?, ?, ?)",
            (last_name, email, paper_title, email_hash),
        )
        return self.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_recipient_by_hash(self, email_hash: str) -> dict[str, Any] | None:
        return self.fetchone("SELECT * FROM recipients WHERE email_hash = ?", (email_hash,))

    # --- Email deliverability checks ---

    def save_check(self, email: str, verdict: str, mx_host: str = "", detail: str = "") -> None:
        self.execute(
            """INSERT INTO email_checks (email, verdict, mx_host, detail)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(email) DO UPDATE SET
                 verdict = excluded.verdict,
                 mx_host = excluded.mx_host,
                 detail = excluded.detail,
                 checked_at = CURRENT_TIMESTAMP""",
            (email, verdict, mx_host, detail),
        )

    def get_check_verdict(self, email: str) -> str:
        row = self.fetchone("SELECT verdict FROM email_checks WHERE email = ?", (email,))
        return row["verdict"] if row else ""

    def count_recipients(self) -> int:
        r = self.fetchone("SELECT COUNT(*) as cnt FROM recipients")
        return r["cnt"] if r else 0

    # --- Sends ---

    def record_send(self, recipient_id: int, account_email: str, status: str,
                    error_type: str = "", error_detail: str = "", latency_ms: int = 0,
                    latency_details: str = "", body_fingerprint: str = "",
                    smtp_conversation: str = "", correlation_id: str = "") -> None:
        self.execute(
            "INSERT INTO sends (recipient_id, account_email, status, error_type, error_detail, latency_ms, latency_details, body_fingerprint, smtp_conversation, correlation_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (recipient_id, account_email, status, error_type, error_detail, latency_ms, latency_details, body_fingerprint, smtp_conversation, correlation_id),
        )

    def get_last_send(self) -> dict[str, Any] | None:
        return self.fetchone("SELECT * FROM sends ORDER BY id DESC LIMIT 1")

    def get_send_stats(self) -> dict[str, int]:
        stats = self.fetchone("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as sent,
                SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed
            FROM sends
        """) or {}
        return {
            "total": stats.get("total", 0),
            "sent": stats.get("sent", 0),
            "failed": stats.get("failed", 0),
        }

    def is_email_sent(self, email_hash: str) -> bool:
        r = self.fetchone("""
            SELECT 1 FROM sends s
            JOIN recipients r ON r.id = s.recipient_id
            WHERE r.email_hash = ? AND s.status = 'success'
            LIMIT 1
        """, (email_hash,))
        return r is not None

    # --- Accounts ---

    def upsert_account(self, email: str, provider: str, server: str, port: int) -> None:
        self.execute("""
            INSERT INTO accounts (email, provider, server, port)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                provider=excluded.provider,
                server=excluded.server,
                port=excluded.port,
                updated_at=CURRENT_TIMESTAMP
        """, (email, provider, server, port))

    def record_account_success(self, email: str, latency_ms: int) -> None:
        now = time.time()
        today = datetime.now().strftime("%Y-%m-%d")
        self.execute("""
            UPDATE accounts SET
                sent_today = CASE WHEN daily_reset_date = ? THEN sent_today + 1 ELSE 1 END,
                sent_total = sent_total + 1,
                last_success = ?,
                avg_latency_ms = (avg_latency_ms * (sent_total - 1) + ?) / sent_total,
                success_rate = (success_rate * sent_total + 1.0) / (sent_total + 1),
                daily_reset_date = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE email = ?
        """, (today, now, latency_ms, today, email))

    def record_account_failure(self, email: str, error_type: str) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        self.execute("""
            UPDATE accounts SET
                failures_today = CASE WHEN daily_reset_date = ?
                    THEN failures_today + 1 ELSE 1 END,
                last_error = ?,
                success_rate = (success_rate * (sent_total + failures_today) + 0.0)
                    / (sent_total + failures_today + 1),
                daily_reset_date = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE email = ?
        """, (today, error_type, today, email))

    def record_auth_failure(self, email: str) -> None:
        self.execute("""
            UPDATE accounts SET
                auth_failures = auth_failures + 1,
                last_error = 'authentication',
                updated_at = CURRENT_TIMESTAMP
            WHERE email = ?
        """, (email,))

    def suspend_account(self, email: str, hours: float) -> None:
        until = time.time() + (hours * 3600.0)
        self.execute(
            "UPDATE accounts SET suspended_until = ?, updated_at = CURRENT_TIMESTAMP WHERE email = ?",
            (until, email),
        )

    def get_all_accounts(self) -> list[dict[str, Any]]:
        return self.fetchall("SELECT * FROM accounts ORDER BY email")

    def get_healthy_accounts(self, exclude_suspended: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM accounts"
        if exclude_suspended:
            query += " WHERE suspended_until <= ? OR suspended_until IS NULL"
            now = time.time()
            return self.fetchall(query, (now,))
        return self.fetchall(query)

    def get_best_account(self) -> dict[str, Any] | None:
        now = time.time()
        return self.fetchone("""
            SELECT * FROM accounts
            WHERE (suspended_until <= ? OR suspended_until IS NULL)
              AND auth_failures < 3
            ORDER BY success_rate DESC, avg_latency_ms ASC, sent_today ASC
            LIMIT 1
        """, (now,))

    # --- Rate Limits ---

    def init_rate_limit(self, provider: str, max_hour: int = 20, max_day: int = 200) -> None:
        self.execute("""
            INSERT INTO rate_limits (provider, max_per_hour, max_per_day)
            VALUES (?, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET
                max_per_hour=excluded.max_per_hour,
                max_per_day=excluded.max_per_day
        """, (provider, max_hour, max_day))

    def check_rate_limit(self, provider: str) -> tuple[bool, str]:
        rl = self.fetchone("SELECT * FROM rate_limits WHERE provider = ?", (provider,))
        if not rl:
            return True, ""

        now = datetime.now()
        hour_window = now.strftime("%Y-%m-%d %H:00:00")
        day_window = now.strftime("%Y-%m-%d 00:00:00")

        if rl["hour_window_start"] != hour_window:
            self.execute(
                "UPDATE rate_limits SET sent_this_hour = 0, hour_window_start = ? WHERE provider = ?",
                (hour_window, provider),
            )
            rl["sent_this_hour"] = 0

        if rl["day_window_start"] != day_window:
            self.execute(
                "UPDATE rate_limits SET sent_this_day = 0, day_window_start = ? WHERE provider = ?",
                (day_window, provider),
            )
            rl["sent_this_day"] = 0

        if rl["sent_this_hour"] >= rl["max_per_hour"]:
            return False, f"Hourly limit ({rl['max_per_hour']}/h) reached for {provider}"
        if rl["sent_this_day"] >= rl["max_per_day"]:
            return False, f"Daily limit ({rl['max_per_day']}/d) reached for {provider}"
        return True, ""

    def increment_rate_limit(self, provider: str) -> None:
        now = datetime.now()
        hour_window = now.strftime("%Y-%m-%d %H:00:00")
        day_window = now.strftime("%Y-%m-%d 00:00:00")

        self.execute("""
            INSERT INTO rate_limits (provider, sent_this_hour, sent_this_day, hour_window_start, day_window_start)
            VALUES (?, 1, 1, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET
                sent_this_hour = sent_this_hour + 1,
                sent_this_day = sent_this_day + 1,
                hour_window_start = ?,
                day_window_start = ?
        """, (provider, hour_window, day_window, hour_window, day_window))

    # --- Bounces ---

    def record_bounce(self, email: str, bounce_type: str, smtp_code: int = 0, diagnostic: str = "") -> None:
        self.execute(
            "INSERT INTO bounces (recipient_email, bounce_type, smtp_code, diagnostic) VALUES (?, ?, ?, ?)",
            (email, bounce_type, smtp_code, diagnostic),
        )

    def get_contact_history(self, email: str) -> dict[str, Any] | None:
        r = self.fetchone("""
            SELECT recipient_id, last_name, paper_title, email_hash
            FROM recipients WHERE email = ?
            LIMIT 1
        """, (email,))
        if not r:
            return None
        sends = self.fetchall("""
            SELECT status, latency_ms, timestamp, error_type
            FROM sends WHERE recipient_id = ?
            ORDER BY id DESC
        """, (r["recipient_id"],))
        bounces = self.fetchall("""
            SELECT bounce_type, smtp_code, timestamp
            FROM bounces WHERE recipient_email = ?
            ORDER BY id DESC
        """, (email,))
        first_send = sends[-1] if sends else None
        last_send = sends[0] if sends else None
        total_sent = sum(1 for s in sends if s["status"] == "success")
        return {
            "last_name": r["last_name"],
            "paper_title": r["paper_title"],
            "email": email,
            "first_contacted": first_send["timestamp"] if first_send else None,
            "last_contacted": last_send["timestamp"] if last_send else None,
            "total_emails_sent": len(sends),
            "successful_sends": total_sent,
            "bounce_count": len(bounces),
            "bounces": bounces[:3],
        }

    def get_domain_reputation(self) -> list[dict[str, Any]]:
        return self.fetchall("""
            SELECT
                SUBSTR(r.email, INSTR(r.email, '@') + 1) AS domain,
                COUNT(*) AS total,
                SUM(CASE WHEN s.status = 'success' THEN 1 ELSE 0 END) AS success,
                ROUND(AVG(s.latency_ms)) AS avg_latency_ms
            FROM sends s
            JOIN recipients r ON r.id = s.recipient_id
            GROUP BY domain
            ORDER BY total DESC
        """)

    def is_bounced(self, email: str) -> bool:
        r = self.fetchone(
            "SELECT 1 FROM bounces WHERE recipient_email = ? AND bounce_type IN ('hard_bounce', 'hard', 'invalid') LIMIT 1",
            (email,),
        )
        return r is not None

    # --- Follow-ups ---

    def mark_replied(self, email: str) -> None:
        self.execute(
            "UPDATE recipients SET last_replied_at = CURRENT_TIMESTAMP WHERE email = ?",
            (email,),
        )

    def increment_followup(self, recipient_id: int) -> None:
        self.execute(
            "UPDATE recipients SET followup_count = followup_count + 1 WHERE id = ?",
            (recipient_id,),
        )

    def get_followup_candidates(self, days: int, max_followups: int = 2) -> list[dict[str, Any]]:
        """Recipients who got a successful send, never replied, and haven't been
        followed up more than max_followups times. Follow-up eligibility starts
        `days` after the last send/follow-up."""
        return self.fetchall("""
            SELECT r.id, r.last_name, r.email, r.paper_title, r.followup_count,
                   last_send.last_send_at,
                   (SELECT COUNT(*) FROM sends s2
                     WHERE s2.recipient_id = r.id AND s2.status = 'success') AS send_count
            FROM recipients r
            JOIN (SELECT recipient_id, MAX(timestamp) AS last_send_at
                  FROM sends WHERE status = 'success' GROUP BY recipient_id) last_send
              ON last_send.recipient_id = r.id
            WHERE (r.last_replied_at IS NULL OR r.last_replied_at = '')
              AND r.followup_count < ?
              AND last_send.last_send_at <= datetime('now', ?)
            ORDER BY last_send.last_send_at ASC
        """, (max_followups, f"-{days} days"))

    def get_followup_summary(self) -> dict[str, int]:
        r = self.fetchone("""
            SELECT
                COUNT(DISTINCT r.id) AS candidates,
                COUNT(DISTINCT CASE WHEN r.last_replied_at IS NOT NULL AND r.last_replied_at != '' THEN r.id END) AS replied,
                COUNT(DISTINCT CASE WHEN r.followup_count > 0 THEN r.id END) AS followed_up
            FROM recipients r
            JOIN sends s ON s.recipient_id = r.id AND s.status = 'success'
        """)
        return {"candidates": r["candidates"] or 0, "replied": r["replied"] or 0,
                "followed_up": r["followed_up"] or 0} if r else {"candidates": 0, "replied": 0, "followed_up": 0}

    # --- Metadata ---

    def set_meta(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO metadata (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def get_meta(self, key: str, default: str = "") -> str:
        r = self.fetchone("SELECT value FROM metadata WHERE key = ?", (key,))
        return r["value"] if r else default

    # --- Progress ---

    def get_progress_index(self) -> int:
        val = self.get_meta("current_index", "0")
        return int(val)

    def set_progress_index(self, idx: int) -> None:
        self.set_meta("current_index", str(idx))

    # --- Maintenance ---

    def vacuum(self) -> None:
        conn = self._get_conn()
        conn.commit()
        conn.execute("VACUUM")

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
