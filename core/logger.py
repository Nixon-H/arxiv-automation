import json
import logging
import os
import re
from datetime import datetime
from typing import Any

CLR_RESET = "\033[0m"
CLR_INFO = "\033[94m"
CLR_SUCCESS = "\033[92m"
CLR_WARN = "\033[93m"
CLR_FAIL = "\033[91m"
CLR_MAGENTA = "\033[95m"

_SECRET_PATTERNS: list = []
_SECRET_INITIALIZED = False


def _init_secret_patterns() -> None:
    global _SECRET_INITIALIZED
    if _SECRET_INITIALIZED:
        return
    patterns = [
        (re.compile(r'(password["\s:=]+)(\S+)', re.IGNORECASE), r'\1***REDACTED***'),
        (re.compile(r'(passwd["\s:=]+)(\S+)', re.IGNORECASE), r'\1***REDACTED***'),
        (re.compile(r'(secret["\s:=]+)(\S+)', re.IGNORECASE), r'\1***REDACTED***'),
        (re.compile(r'(api[ _-]?key["\s:=]+)(\S+)', re.IGNORECASE), r'\1***REDACTED***'),
        (re.compile(r'(token["\s:=]+)(\S+)', re.IGNORECASE), r'\1***REDACTED***'),
        (re.compile(r'(auth["\s:=]+)(\S+)', re.IGNORECASE), r'\1***REDACTED***'),
        (re.compile(r'\b[A-Za-z0-9+/]{40,}={0,2}\b'), '***BASE64-REDACTED***'),
        (re.compile(r'\b[0-9a-f]{40}\b'), '***SHA1-REDACTED***'),
        (re.compile(r'SMTP_PASSWORD_\d+'), '***SMTP_PWD_ENV***'),
    ]
    for p in os.environ:
        if 'PASSWORD' in p.upper() or 'SECRET' in p.upper() or 'TOKEN' in p.upper() or 'KEY' in p.upper():
            val = os.environ[p]
            if len(val) > 4:
                patterns.append((re.compile(re.escape(val)), f'***{p}***'))
    _SECRET_PATTERNS.extend(patterns)
    _SECRET_INITIALIZED = True


def redact_secrets(text: str) -> str:
    _init_secret_patterns()
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class StructuredLogRecord:
    __slots__ = ("timestamp", "level", "message", "recipient", "account", "status", "latency", "correlation_id", "extra")

    def __init__(
        self,
        level: str,
        message: str,
        recipient: str | None = None,
        account: str | None = None,
        status: str | None = None,
        latency: float | None = None,
        correlation_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.timestamp = datetime.now().isoformat()
        self.level = level
        self.message = message
        self.recipient = recipient
        self.account = account
        self.status = status
        self.latency = latency
        self.correlation_id = correlation_id
        self.extra = extra or {}

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "timestamp": self.timestamp,
            "level": self.level,
            "message": self.message,
        }
        if self.recipient:
            d["recipient"] = self.recipient
        if self.account:
            d["account"] = self.account
        if self.status:
            d["status"] = self.status
        if self.latency is not None:
            d["latency"] = self.latency
        if self.correlation_id:
            d["correlation_id"] = self.correlation_id
        if self.extra:
            d["extra"] = self.extra
        return d


class AppLogger:
    _log_dir = "logs"
    _structured_log_file: str | None = None

    @classmethod
    def initialize(cls, log_dir: str = "logs") -> None:
        cls._log_dir = log_dir
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        log_filename = f"{log_dir}/{datetime.now().strftime('%Y-%m-%d')}.log"
        cls._structured_log_file = f"{log_dir}/{datetime.now().strftime('%Y-%m-%d')}.jsonl"

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(log_filename, encoding="utf-8"),
                logging.NullHandler(),
            ],
        )

    @classmethod
    def _write_structured(cls, record: StructuredLogRecord) -> None:
        if cls._structured_log_file:
            try:
                with open(cls._structured_log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record.to_dict()) + "\n")
            except Exception:
                pass

    @classmethod
    def info(cls, msg: str, **meta: Any) -> None:
        safe = redact_secrets(msg)
        record = StructuredLogRecord("INFO", safe, **meta)
        logging.info(safe)
        cls._write_structured(record)
        print(f"{CLR_INFO}[INFO] [{datetime.now().strftime('%H:%M:%S')}] {safe}{CLR_RESET}")

    @classmethod
    def success(cls, msg: str, **meta: Any) -> None:
        safe = redact_secrets(msg)
        record = StructuredLogRecord("SUCCESS", safe, **meta)
        logging.info(f"SUCCESS: {safe}")
        cls._write_structured(record)
        print(f"{CLR_SUCCESS}[SUCC] [{datetime.now().strftime('%H:%M:%S')}] {safe}{CLR_RESET}")

    @classmethod
    def warn(cls, msg: str, **meta: Any) -> None:
        safe = redact_secrets(msg)
        record = StructuredLogRecord("WARN", safe, **meta)
        logging.warning(safe)
        cls._write_structured(record)
        print(f"{CLR_WARN}[WARN] [{datetime.now().strftime('%H:%M:%S')}] {safe}{CLR_RESET}")

    @classmethod
    def error(cls, msg: str, **meta: Any) -> None:
        safe = redact_secrets(msg)
        record = StructuredLogRecord("ERROR", safe, **meta)
        logging.error(safe, exc_info=True)
        cls._write_structured(record)
        print(f"{CLR_FAIL}[ERR!] [{datetime.now().strftime('%H:%M:%S')}] {safe}{CLR_RESET}")

    @classmethod
    def debug(cls, msg: str) -> None:
        print(f"{CLR_MAGENTA}[DEBG] {redact_secrets(msg)}{CLR_RESET}")
