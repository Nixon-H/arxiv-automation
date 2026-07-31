import os
import ssl
import time
import json
import random
import hashlib
import smtplib
import threading
from enum import Enum
from typing import Dict, Any, List, Optional, Tuple
from email.header import Header
from email.utils import formataddr, make_msgid, formatdate
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

from core.logger import AppLogger
from core.database import Database
from core.ratelimiter import RateLimiter
from core.dns_validator import validate_email_dns
from core.config_typed import SMTPAccount
from core.exceptions import SmtpTransmissionError
from parsing.bounce import BounceClassifier, SMTP_RESPONSE_MAP


class SmtpErrorClass(Enum):
    TEMPORARY = "temporary"
    PERMANENT = "permanent"
    AUTHENTICATION = "authentication"
    DNS = "dns"
    TLS = "tls"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    BOUNCE_HARD = "hard_bounce"
    BOUNCE_SOFT = "soft_bounce"
    UNKNOWN = "unknown"


RETRYABLE = {
    SmtpErrorClass.TEMPORARY,
    SmtpErrorClass.TIMEOUT,
    SmtpErrorClass.RATE_LIMITED,
    SmtpErrorClass.DNS,
    SmtpErrorClass.BOUNCE_SOFT,
    SmtpErrorClass.UNKNOWN,
}


def classify_smtp_error(exception: Exception, smtp_code: int = 0) -> SmtpErrorClass:
    msg = str(exception).lower()

    if isinstance(exception, smtplib.SMTPAuthenticationError):
        return SmtpErrorClass.AUTHENTICATION
    if isinstance(exception, smtplib.SMTPConnectError):
        return SmtpErrorClass.DNS
    if isinstance(exception, smtplib.SMTPHeloError):
        return SmtpErrorClass.TLS
    if isinstance(exception, smtplib.SMTPServerDisconnected):
        return SmtpErrorClass.TEMPORARY
    if isinstance(exception, smtplib.SMTPDataError):
        return SmtpErrorClass.PERMANENT

    if isinstance(exception, smtplib.SMTPResponseException):
        code = exception.smtp_code
        info = SMTP_RESPONSE_MAP.get(code, {})
        bounce_type = info.get("bounce_type", "")
        if bounce_type == "hard_bounce":
            return SmtpErrorClass.BOUNCE_HARD
        if bounce_type in ("temporary", "retry"):
            return SmtpErrorClass.TEMPORARY
        if bounce_type == "authentication":
            return SmtpErrorClass.AUTHENTICATION
        if bounce_type == "mailbox_full":
            return SmtpErrorClass.BOUNCE_SOFT
        if bounce_type == "spam_rejection":
            return SmtpErrorClass.PERMANENT

    if isinstance(exception, ssl.SSLError):
        return SmtpErrorClass.TLS
    if isinstance(exception, TimeoutError):
        return SmtpErrorClass.TIMEOUT
    if isinstance(exception, ConnectionRefusedError):
        return SmtpErrorClass.DNS

    if isinstance(exception, OSError):
        if "timeout" in msg:
            return SmtpErrorClass.TIMEOUT
        if "refused" in msg or "dns" in msg or "resolve" in msg or "unknown host" in msg:
            return SmtpErrorClass.DNS

    if "rate limit" in msg or "too many" in msg or "throttle" in msg:
        return SmtpErrorClass.RATE_LIMITED
    if "temporarily" in msg or "try again" in msg:
        return SmtpErrorClass.TEMPORARY
    if "blocked" in msg or "spam" in msg or "blacklist" in msg:
        return SmtpErrorClass.PERMANENT
    if "does not exist" in msg or "invalid" in msg or "not found" in msg:
        return SmtpErrorClass.BOUNCE_HARD

    if smtp_code:
        if 400 <= smtp_code < 500:
            return SmtpErrorClass.TEMPORARY
        if smtp_code >= 500:
            return SmtpErrorClass.PERMANENT

    return SmtpErrorClass.UNKNOWN


PROVIDER_PATTERNS = {
    "Google Workspace/Gmail": ["google", "gmail", "googlemail"],
    "Microsoft 365/Outlook": ["microsoft", "outlook", "office365", "hotmail", "live"],
    "Exchange": ["exchange", "msexch"],
    "Postfix": ["postfix"],
    "Exim": ["exim"],
    "Sendmail": ["sendmail"],
    "Zimbra": ["zimbra"],
    "Cpanel/WHM": ["cpanel", "whm", "md-in-"],
    "Proofpoint": ["proofpoint", "pp-hosted"],
    "Yahoo": ["yahoo"],
    "Yandex": ["yandex"],
    "ProtonMail": ["protonmail", "proton"],
}


def detect_smtp_provider(banner: str) -> str:
    banner_lower = banner.lower()
    for provider, patterns in PROVIDER_PATTERNS.items():
        for pat in patterns:
            if pat in banner_lower:
                return provider
    return "Unknown/Generic"


def detect_provider_from_domain(email: str) -> str:
    domain = email.split("@")[-1].lower()
    if "gmail" in domain:
        return "Google Workspace/Gmail"
    if "outlook" in domain or "hotmail" in domain or "live" in domain:
        return "Microsoft 365/Outlook"
    if "yahoo" in domain:
        return "Yahoo"
    if "proton" in domain:
        return "ProtonMail"
    if "yandex" in domain:
        return "Yandex"
    if "icloud" in domain or "me.com" in domain:
        return "Apple iCloud"
    if "zoho" in domain:
        return "Zoho"
    return "Unknown/Generic"


CAPABILITY_LABELS = {
    "starttls": "STARTTLS",
    "auth": "AUTH",
    "size": "SIZE",
    "pipelining": "PIPELINING",
    "8bitmime": "8BITMIME",
    "smtputf8": "SMTPUTF8",
    "dsn": "DSN",
    "deliverybystatus": "DELIVERYBY",
    "requiretls": "REQUIRETLS",
    "chunking": "CHUNKING",
    "binarymime": "BINARYMIME",
}


def format_capabilities(caps: Dict[str, str]) -> str:
    parts = []
    for key, label in CAPABILITY_LABELS.items():
        if key in caps:
            val = caps[key]
            if key == "auth":
                parts.append(f"AUTH {val.upper()}")
            elif key == "size":
                parts.append(f"SIZE {val}")
            else:
                parts.append(f"{label} ✓")
    return " | ".join(parts) if parts else "None detected"


class SmtpConnectionPool:
    def __init__(self, accounts: List[SMTPAccount], timeout: float = 30.0) -> None:
        self.accounts = accounts
        self.timeout = timeout
        self._lock = threading.Lock()
        self._connections: Dict[str, smtplib.SMTP] = {}
        self._capabilities: Dict[str, Dict[str, Any]] = {}

    def detect_capabilities(self, server: smtplib.SMTP, account_email: str) -> Dict[str, str]:
        caps: Dict[str, str] = {}
        for key in CAPABILITY_LABELS:
            val = server.esmtp_features.get(key)
            if val is not None:
                caps[key] = val
        self._capabilities[account_email] = caps
        fmt = format_capabilities(caps)
        AppLogger.info(f"SMTP capabilities [{account_email}]: {fmt}")
        return caps

    def get_account_capabilities(self, account_email: str) -> Dict[str, str]:
        return self._capabilities.get(account_email, {})

    def has_capability(self, account_email: str, capability: str) -> bool:
        caps = self._capabilities.get(account_email, {})
        return capability.lower() in caps

    def all_capabilities(self) -> Dict[str, Dict[str, str]]:
        return dict(self._capabilities)

    def _get_cert_info(self, server: smtplib.SMTP) -> Dict[str, Any]:
        cert_info: Dict[str, Any] = {"valid": False, "issuer": "", "subject": "", "expiry": ""}
        try:
            sock = server.sock
            if sock:
                tls_sock = sock
                # walk through SSL wrapping layers
                for _ in range(5):
                    if hasattr(tls_sock, "_sslobj") and tls_sock._sslobj:
                        break
                    if hasattr(tls_sock, "sock"):
                        tls_sock = tls_sock.sock
                if hasattr(tls_sock, "getpeercert"):
                    cert = tls_sock.getpeercert()
                    if cert:
                        cert_info["valid"] = True
                        issuer = dict(x[0] for x in cert.get("issuer", []))
                        subject = dict(x[0] for x in cert.get("subject", []))
                        cert_info["issuer"] = issuer.get("organizationName", issuer.get("commonName", "Unknown"))
                        cert_info["subject"] = subject.get("commonName", "")
                        cert_info["expiry"] = cert.get("notAfter", "")
        except Exception:
            pass
        return cert_info

    def _connect(self, acct: SMTPAccount) -> Optional[smtplib.SMTP]:
        phases: Dict[str, int] = {}
        t0 = int(time.time() * 1000)
        try:
            context = ssl.create_default_context()
            server = smtplib.SMTP(timeout=self.timeout)
            banner_code, banner_msg = server.connect(acct.server, acct.port)
            server._host = acct.server
            phases["smtp_connect"] = int(time.time() * 1000) - t0

            # Provider detection from banner
            try:
                provider = detect_smtp_provider(str(banner_msg))
                domain_provider = detect_provider_from_domain(acct.email)
                detected = provider if provider != "Unknown/Generic" else domain_provider
                AppLogger.info(f"SMTP provider [{acct.email}]: {detected} (banner says: {provider}, domain suggests: {domain_provider})")
                self._capabilities[acct.email + "_provider"] = detected
            except Exception:
                pass

            t1 = int(time.time() * 1000)
            server.ehlo()
            phases["ehlo"] = int(time.time() * 1000) - t1

            t2 = int(time.time() * 1000)
            server.starttls(context=context)
            phases["tls_handshake"] = int(time.time() * 1000) - t2

            cert_info = self._get_cert_info(server)
            if cert_info["valid"]:
                AppLogger.info(
                    f"TLS cert [{acct.email}]: issuer={cert_info['issuer']}, "
                    f"subject={cert_info['subject']}, expiry={cert_info['expiry']}"
                )
            else:
                AppLogger.warn(f"TLS cert [{acct.email}]: could not retrieve certificate info")

            t3 = int(time.time() * 1000)
            server.ehlo()
            phases["ehlo_post_tls"] = int(time.time() * 1000) - t3

            self.detect_capabilities(server, acct.email)

            t4 = int(time.time() * 1000)
            server.login(acct.email, acct.password)
            phases["auth"] = int(time.time() * 1000) - t4

            phases["connect_total"] = int(time.time() * 1000) - t0
            self._capabilities[acct.email + "_phases"] = phases
            return server
        except Exception as e:
            AppLogger.warn(f"SMTP connect failed for {acct.email}: {e}")
            return None

    def get_connection(self, account_idx: int) -> Optional[smtplib.SMTP]:
        if account_idx >= len(self.accounts):
            return None
        acct = self.accounts[account_idx]
        email = acct.email

        with self._lock:
            conn = self._connections.get(email)
            if conn is not None:
                try:
                    conn.noop()
                    return conn
                except Exception:
                    self._connections.pop(email, None)

            conn = self._connect(acct)
            if conn:
                self._connections[email] = conn
            return conn

    def close_all(self) -> None:
        with self._lock:
            for email, conn in self._connections.items():
                try:
                    conn.quit()
                except Exception:
                    pass
            self._connections.clear()


class SmtpEngine:
    def __init__(
        self,
        accounts: List[SMTPAccount],
        db: Database,
        rate_limiter: RateLimiter,
        timeout: float = 30.0,
    ) -> None:
        self.accounts = accounts
        self.db = db
        self.rate_limiter = rate_limiter
        self.timeout = timeout
        self.bounce = BounceClassifier(db)
        self.pool = SmtpConnectionPool(accounts, timeout)

        for acct in accounts:
            self.db.upsert_account(acct.email, acct.provider, acct.server, acct.port)
            self.rate_limiter.register_provider(
                acct.provider, acct.max_per_hour, acct.max_per_day
            )

    def send_atomic(
        self,
        account_idx: int,
        recipient_email: str,
        subject: str,
        text_content: str,
        html_content: str,
        attachment_path: Optional[str] = None,
        reply_to: Optional[str] = None,
        custom_headers: Optional[Dict[str, str]] = None,
        correlation_id: str = "",
    ) -> Tuple[bool, SmtpErrorClass, int, str, Dict[str, int]]:
        phases: Dict[str, int] = {}
        start_ms = int(time.time() * 1000)

        if account_idx >= len(self.accounts):
            return False, SmtpErrorClass.UNKNOWN, 0, "No account", phases

        acct = self.accounts[account_idx]

        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = formataddr((
                str(Header(acct.display_name, "utf-8")), acct.email
            ))
            msg["To"] = recipient_email
            msg["Subject"] = Header(subject, "utf-8")
            msg["Message-ID"] = make_msgid()
            msg["Date"] = formatdate(localtime=True)
            msg["X-Mailer"] = "ArxivDispatch/5.0"
            msg["User-Agent"] = "ArxivDispatch/5.0"

            if reply_to:
                msg["Reply-To"] = reply_to
            if custom_headers:
                for k, v in custom_headers.items():
                    msg[k] = v

            msg.attach(MIMEText(text_content, "plain", "utf-8"))
            msg.attach(MIMEText(html_content, "html", "utf-8"))

            if attachment_path and os.path.exists(attachment_path):
                wrapper = MIMEMultipart("mixed")
                wrapper.attach(MIMEText(text_content, "plain", "utf-8"))
                wrapper.attach(MIMEText(html_content, "html", "utf-8"))

                with open(attachment_path, "rb") as f:
                    part = MIMEApplication(f.read(), _subtype="pdf")
                    part.add_header(
                        "Content-Disposition", "attachment",
                        filename=os.path.basename(attachment_path),
                    )
                    wrapper.attach(part)

                for h in ("From", "To", "Subject", "Message-ID", "Date", "Reply-To", "X-Mailer"):
                    if msg[h]:
                        wrapper[h] = msg[h]
                msg = wrapper

            server = self.pool.get_connection(account_idx)
            if server is None:
                return False, SmtpErrorClass.DNS, 0, "Connection failed", phases

            pool_phases = self.pool._capabilities.get(acct.email + "_phases", {})
            phases.update(pool_phases)

            phases["build_msg"] = int(time.time() * 1000) - start_ms
            t_send = int(time.time() * 1000)
            server.sendmail(acct.email, recipient_email, msg.as_string())
            phases["sendmail"] = int(time.time() * 1000) - t_send
            latency = int(time.time() * 1000) - start_ms
            phases["total"] = latency
            return True, SmtpErrorClass.TEMPORARY, latency, "", phases

        except smtplib.SMTPResponseException as e:
            latency = int(time.time() * 1000) - start_ms
            phases["total"] = latency
            pool_phases = self.pool._capabilities.get(acct.email + "_phases", {})
            phases.update(pool_phases)
            bounce_type = self.bounce.record(recipient_email, e.smtp_code, str(e))
            err_class = classify_smtp_error(e, e.smtp_code)
            return False, err_class, latency, f"SMTP {e.smtp_code}: {e.smtp_error}", phases

        except Exception as e:
            latency = int(time.time() * 1000) - start_ms
            phases["total"] = latency
            pool_phases = self.pool._capabilities.get(acct.email + "_phases", {})
            phases.update(pool_phases)
            err_class = classify_smtp_error(e)
            msg_str = str(e)[:200]
            return False, err_class, latency, msg_str, phases

    def _body_fingerprint(self, text: str) -> str:
        return hashlib.sha256(text.lower().strip().encode("utf-8")).hexdigest()[:16]

    def send_with_adaptive_routing(
        self,
        recipient_email: str,
        subject: str,
        text_content: str,
        html_content: str,
        recipient_id: int = 0,
        attachment_path: Optional[str] = None,
        reply_to: Optional[str] = None,
        custom_headers: Optional[Dict[str, str]] = None,
        dns_check: bool = True,
        correlation_id: str = "",
    ) -> bool:
        t_dns_start = int(time.time() * 1000)
        if dns_check:
            dns_ok, dns_msg = validate_email_dns(recipient_email)
            if not dns_ok:
                AppLogger.warn(f"DNS validation failed for {recipient_email}: {dns_msg}")
                self.db.record_bounce(recipient_email, "dns", 0, dns_msg)
                return False
        dns_ms = int(time.time() * 1000) - t_dns_start

        if self.db.is_bounced(recipient_email):
            AppLogger.warn(f"Skipping previously bounced: {recipient_email}")
            return False

        max_retries = 3
        base_backoff = 2.0

        for attempt in range(max_retries + 1):
            acct = self.db.get_best_account()
            if not acct:
                AppLogger.error("No healthy accounts available")
                return False

            account_email = acct["email"]
            provider = acct["provider"]
            account_idx = next(
                (i for i, a in enumerate(self.accounts) if a.email == account_email),
                None,
            )
            if account_idx is None:
                continue

            ok, limit_msg = self.rate_limiter.check(provider)
            if not ok:
                AppLogger.warn(f"Rate limit: {limit_msg}")
                acct_idx_alt = (account_idx + 1) % len(self.accounts)
                if acct_idx_alt != account_idx:
                    alt = self.accounts[acct_idx_alt]
                    AppLogger.info(f"Failing over to {alt.email}")
                    account_idx = acct_idx_alt
                    account_email = alt.email
                    provider = alt.provider
                else:
                    time.sleep(60)
                    continue

            success, err_class, latency_ms, err_msg, phases = self.send_atomic(
                account_idx=account_idx,
                recipient_email=recipient_email,
                subject=subject,
                text_content=text_content,
                html_content=html_content,
                attachment_path=attachment_path,
                reply_to=reply_to,
                custom_headers=custom_headers,
                correlation_id=correlation_id,
            )

            body_fp = self._body_fingerprint(text_content)

            if success:
                self.rate_limiter.increment(provider)
                self.db.record_account_success(account_email, latency_ms)
                phases["dns"] = dns_ms
                self.db.record_send(
                    recipient_id, account_email, "success",
                    latency_ms=latency_ms,
                    latency_details=json.dumps(phases),
                    body_fingerprint=body_fp,
                    correlation_id=correlation_id,
                )
                AppLogger.success(
                    f"Sent via {account_email} to {recipient_email} ({latency_ms}ms)",
                    recipient=recipient_email, account=account_email,
                    status="SUCCESS", latency=latency_ms / 1000.0,
                    correlation_id=correlation_id,
                )
                return True

            self.db.record_account_failure(account_email, err_class.value)
            phases["dns"] = dns_ms
            self.db.record_send(
                recipient_id, account_email, "failed",
                error_type=err_class.value, error_detail=err_msg,
                latency_ms=latency_ms,
                latency_details=json.dumps(phases),
                body_fingerprint=body_fp,
                correlation_id=correlation_id,
            )
            AppLogger.warn(
                f"Fail via {account_email}: {err_class.value} ({latency_ms}ms): {err_msg[:80]}",
                recipient=recipient_email, account=account_email, status="FAIL",
                correlation_id=correlation_id,
            )

            if err_class == SmtpErrorClass.AUTHENTICATION:
                self.db.record_auth_failure(account_email)
                self.db.suspend_account(account_email, 12.0)
                AppLogger.error(f"Auth fail on {account_email} — suspended 12h")
                continue

            if err_class == SmtpErrorClass.BOUNCE_HARD:
                AppLogger.error(f"Hard bounce for {recipient_email} — removing")
                return False

            if err_class == SmtpErrorClass.PERMANENT:
                AppLogger.error(f"Permanent failure — skipping")
                return False

            if err_class == SmtpErrorClass.RATE_LIMITED:
                self.db.suspend_account(account_email, 6.0)
                AppLogger.warn(f"Rate limited on {account_email} — suspended 6h")
                continue

            if err_class in RETRYABLE and attempt < max_retries:
                delay = base_backoff * (2 ** attempt) * random.uniform(0.8, 1.5)
                AppLogger.info(f"Retry {attempt + 1}/{max_retries} in {delay:.1f}s...")
                time.sleep(delay)

        AppLogger.error(
            f"All attempts exhausted for {recipient_email}",
            recipient=recipient_email, status="FAIL",
        )
        return False

    def close(self) -> None:
        self.pool.close_all()
