import os
import tempfile

from core.validator import (
    file_checksum,
    generate_sha256,
    normalize_unicode,
    pre_flight_checks,
    validate_email_format,
    validate_pdf,
    verify_file_integrity,
)


class TestValidateEmail:
    def test_valid_emails(self):
        assert validate_email_format("user@example.com") is True
        assert validate_email_format("a.b@c.co") is True
        assert validate_email_format("user+tag@domain.org") is True

    def test_invalid_emails(self):
        assert validate_email_format("") is False
        assert validate_email_format("notanemail") is False
        assert validate_email_format("@nouser.com") is False
        assert validate_email_format("user@") is False
        assert validate_email_format("user@.com") is False


class TestGenerateSha256:
    def test_sha256_consistent(self):
        h1 = generate_sha256("test@example.com")
        h2 = generate_sha256("test@example.com")
        assert h1 == h2

    def test_sha256_case_insensitive(self):
        h1 = generate_sha256("Test@Example.Com")
        h2 = generate_sha256("test@example.com")
        assert h1 == h2

    def test_sha256_different(self):
        h1 = generate_sha256("a@b.com")
        h2 = generate_sha256("c@d.com")
        assert h1 != h2

    def test_sha256_empty(self):
        h = generate_sha256("")
        assert isinstance(h, str)
        assert len(h) == 64


class TestFileChecksum:
    def test_checksum_consistent(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("hello world")
            p = f.name
        try:
            c1 = file_checksum(p)
            c2 = file_checksum(p)
            assert c1 == c2
            assert len(c1) == 64
        finally:
            os.unlink(p)

    def test_checksum_file_not_found(self):
        assert file_checksum("/nonexistent/file") is None

    def test_checksum_md5(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("content")
            p = f.name
        try:
            c = file_checksum(p, "md5")
            assert c is not None
            assert len(c) == 32
        finally:
            os.unlink(p)


class TestVerifyFileIntegrity:
    def test_missing_file(self):
        ok, msg = verify_file_integrity("/nonexistent/file")
        assert ok is False
        assert "not found" in msg.lower()

    def test_checksum_mismatch(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("content")
            p = f.name
            fake_checksum = "0" * 64
        try:
            ok, msg = verify_file_integrity(p, fake_checksum)
            assert ok is False
            assert "mismatch" in msg.lower()
        finally:
            os.unlink(p)

    def test_verify_missing_pdf_checksum(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("content")
            p = f.name
        try:
            ok, msg = verify_file_integrity(p)
            assert ok is True
        finally:
            os.unlink(p)


class TestValidatePdf:
    def test_not_a_pdf(self):
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            f.write(b"not a pdf content here")
            p = f.name
        try:
            ok, msg = validate_pdf(p)
            assert ok is False
            assert "PDF" in msg
        finally:
            os.unlink(p)

    def test_valid_pdf_header(self):
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            f.write(b"%PDF-1.4\n...")
            p = f.name
        try:
            ok, msg = validate_pdf(p)
            assert ok is True
        finally:
            os.unlink(p)

    def test_pdf_not_found(self):
        ok, msg = validate_pdf("/nonexistent.pdf")
        assert ok is False


class TestPreFlight:
    def test_pre_flight_all_pass(self):
        checks = [("A", True, "ok"), ("B", True, "ok")]
        assert pre_flight_checks(checks) is True

    def test_pre_flight_any_fail(self):
        checks = [("A", True, "ok"), ("B", False, "fail")]
        assert pre_flight_checks(checks) is False

    def test_pre_flight_empty(self):
        assert pre_flight_checks([]) is True


class TestNormalizeUnicode:
    def test_ascii_passthrough(self):
        assert normalize_unicode("Hello") == "Hello"

    def test_accented_chars(self):
        result = normalize_unicode("José")
        assert "Jose" in result or "José" in result

    def test_whitespace_stripped(self):
        assert normalize_unicode("  hello  ") == "hello"

    def test_empty_string(self):
        assert normalize_unicode("") == ""
