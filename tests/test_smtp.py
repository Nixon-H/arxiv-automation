from engine.smtp import classify_smtp_error, SmtpErrorClass, format_capabilities, CAPABILITY_LABELS
import smtplib
import ssl


def test_classify_auth_error():
    exc = smtplib.SMTPAuthenticationError(535, b"auth failed")
    cls = classify_smtp_error(exc)
    assert cls == SmtpErrorClass.AUTHENTICATION


def test_classify_connect_error():
    exc = smtplib.SMTPConnectError(421, b"service unavailable")
    cls = classify_smtp_error(exc)
    assert cls == SmtpErrorClass.DNS


def test_classify_ssl_error():
    exc = ssl.SSLError("certificate verify failed")
    cls = classify_smtp_error(exc)
    assert cls == SmtpErrorClass.TLS


def test_classify_timeout():
    exc = TimeoutError("timed out")
    cls = classify_smtp_error(exc)
    assert cls == SmtpErrorClass.TIMEOUT


def test_classify_rate_limit():
    exc = Exception("rate limit exceeded")
    cls = classify_smtp_error(exc)
    assert cls == SmtpErrorClass.RATE_LIMITED


def test_classify_unknown():
    exc = Exception("weird error")
    cls = classify_smtp_error(exc)
    assert cls == SmtpErrorClass.UNKNOWN


def test_classify_smtp_code_4xx():
    exc = smtplib.SMTPResponseException(450, b"mailbox busy")
    cls = classify_smtp_error(exc)
    assert cls == SmtpErrorClass.TEMPORARY


def test_classify_smtp_code_5xx():
    exc = smtplib.SMTPResponseException(550, b"mailbox not found")
    cls = classify_smtp_error(exc)
    assert cls == SmtpErrorClass.BOUNCE_HARD


def test_format_capabilities():
    caps = {"starttls": "", "auth": "LOGIN PLAIN", "size": "35882577", "pipelining": ""}
    fmt = format_capabilities(caps)
    assert "STARTTLS" in fmt
    assert "AUTH LOGIN PLAIN" in fmt
    assert "SIZE" in fmt
    assert "PIPELINING" in fmt


def test_format_capabilities_empty():
    assert format_capabilities({}) == "None detected"


def test_retryable_set():
    assert SmtpErrorClass.TEMPORARY in SmtpErrorClass._retryable
    assert SmtpErrorClass.TIMEOUT in SmtpErrorClass._retryable


SmtpErrorClass._retryable = {
    SmtpErrorClass.TEMPORARY,
    SmtpErrorClass.TIMEOUT,
    SmtpErrorClass.RATE_LIMITED,
    SmtpErrorClass.DNS,
    SmtpErrorClass.BOUNCE_SOFT,
    SmtpErrorClass.UNKNOWN,
}