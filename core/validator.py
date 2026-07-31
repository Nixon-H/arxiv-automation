import os
import re
import hashlib
import unicodedata
from typing import Optional, Tuple

from core.exceptions import IntegrityCheckError


EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def validate_email_format(email: str) -> bool:
    return bool(EMAIL_REGEX.match(email.strip()))


def normalize_unicode(text: str) -> str:
    return unicodedata.normalize("NFKC", text.strip())


def generate_sha256(text: str) -> str:
    return hashlib.sha256(text.lower().strip().encode("utf-8")).hexdigest()


def file_checksum(file_path: str, algorithm: str = "sha256") -> Optional[str]:
    if not os.path.exists(file_path):
        return None
    h = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_file_integrity(file_path: str, expected_checksum: Optional[str] = None,
                          max_size_mb: float = 10.0) -> Tuple[bool, str]:
    if not os.path.exists(file_path):
        return False, f"File not found: {file_path}"

    size_mb = os.path.getsize(file_path) / (1024 * 1024)
    if size_mb > max_size_mb:
        return False, f"File too large: {size_mb:.1f}MB (max {max_size_mb}MB)"

    actual = file_checksum(file_path)
    if expected_checksum and actual and actual != expected_checksum:
        return False, f"Checksum mismatch: expected {expected_checksum}, got {actual}"

    return True, f"OK ({size_mb:.1f}MB)"


def validate_pdf(file_path: str) -> Tuple[bool, str]:
    if not os.path.exists(file_path):
        return False, "PDF not found"
    with open(file_path, "rb") as f:
        header = f.read(5)
    if header != b"%PDF-":
        return False, "Not a valid PDF (missing %PDF- header)"
    return True, "Valid PDF"


def pre_flight_checks(checks: list) -> bool:
    all_ok = True
    for name, ok, msg in checks:
        status = "PASS" if ok else "FAIL"
        color = "" if ok else ""
        reset = ""
        print(f"  [{status}] {name}: {msg}")
        if not ok:
            all_ok = False
    return all_ok
