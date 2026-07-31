import re
from typing import Dict, Tuple, Optional

from core.database import Database
from core.logger import AppLogger


SMTP_RESPONSE_MAP: Dict[int, Dict[str, str]] = {
    211: {"label": "System Status", "action": "info"},
    214: {"label": "Help Message", "action": "info"},
    220: {"label": "Service Ready", "action": "ok"},
    221: {"label": "Closing Channel", "action": "ok"},
    235: {"label": "Authentication Successful", "action": "ok"},
    250: {"label": "Request Completed", "action": "ok"},
    251: {"label": "User Not Local, Forwarding", "action": "ok"},
    252: {"label": "Cannot VRFY User", "action": "info"},
    334: {"label": "Authentication Challenge", "action": "auth"},
    354: {"label": "Start Mail Input", "action": "ok"},
    421: {"label": "Service Unavailable", "action": "retry", "bounce_type": "temporary"},
    450: {"label": "Mailbox Unavailable", "action": "retry", "bounce_type": "temporary"},
    451: {"label": "Local Processing Error", "action": "retry", "bounce_type": "temporary"},
    452: {"label": "Insufficient Storage", "action": "retry", "bounce_type": "temporary"},
    455: {"label": "Server Paused", "action": "retry", "bounce_type": "temporary"},
    500: {"label": "Syntax Error", "action": "fail", "bounce_type": "permanent"},
    501: {"label": "Syntax Error in Params", "action": "fail", "bounce_type": "permanent"},
    502: {"label": "Command Not Implemented", "action": "fail", "bounce_type": "permanent"},
    503: {"label": "Bad Command Sequence", "action": "fail", "bounce_type": "permanent"},
    504: {"label": "Command Parameter Not Implemented", "action": "fail", "bounce_type": "permanent"},
    521: {"label": "Server Does Not Accept Mail", "action": "fail", "bounce_type": "permanent"},
    530: {"label": "Authentication Required", "action": "auth", "bounce_type": "authentication"},
    535: {"label": "Authentication Failed", "action": "auth", "bounce_type": "authentication"},
    541: {"label": "Recipient Rejected", "action": "fail", "bounce_type": "hard_bounce"},
    550: {"label": "Mailbox Not Found", "action": "fail", "bounce_type": "hard_bounce"},
    551: {"label": "User Not Local", "action": "fail", "bounce_type": "hard_bounce"},
    552: {"label": "Mailbox Full", "action": "fail", "bounce_type": "mailbox_full"},
    553: {"label": "Mailbox Name Invalid", "action": "fail", "bounce_type": "hard_bounce"},
    554: {"label": "Transaction Failed", "action": "fail", "bounce_type": "spam_rejection"},
    555: {"label": "MAIL FROM/RCPT TO Problems", "action": "fail", "bounce_type": "permanent"},
}


_TEXT_PATTERNS: list = [
    (r"(?i)(mailbox|user|account|recipient).*(not found|doesn't exist|invalid|unknown)", "hard_bounce"),
    (r"(?i)(mailbox|storage|quota|space).*(full|exceeded|over)", "mailbox_full"),
    (r"(?i)(rate.limit|too many|throttle|exceeded)", "rate_limited"),
    (r"(?i)(spam|blacklist|blocked|rejected)", "spam_rejection"),
    (r"(?i)(temporar|try again|later|busy)", "temporary"),
    (r"(?i)(greylist|grey.list)", "greylisting"),
    (r"(?i)(auth|login|password|credential)", "authentication"),
    (r"(?i)(dns|resolve|not found|domain)", "dns"),
    (r"(?i)(timeout|timed out)", "timeout"),
    (r"(?i)(certificate|tls|ssl)", "tls"),
]


class BounceClassifier:
    def __init__(self, db: Database) -> None:
        self.db = db

    def classify(self, smtp_code: int, error_message: str) -> str:
        code_info = SMTP_RESPONSE_MAP.get(smtp_code, {})
        if code_info.get("bounce_type"):
            return code_info["bounce_type"]

        for pattern, bounce_type in _TEXT_PATTERNS:
            if re.search(pattern, error_message):
                return bounce_type

        if 400 <= smtp_code < 500:
            return "temporary"
        if smtp_code >= 500:
            return "permanent"

        return "unknown"

    def record(self, email: str, smtp_code: int, error_message: str) -> str:
        bounce_type = self.classify(smtp_code, error_message)
        self.db.record_bounce(email, bounce_type, smtp_code, error_message[:500])
        AppLogger.info(f"Bounce recorded: {email} -> {bounce_type} (code {smtp_code})")
        return bounce_type

    def get_recovery_advice(self, bounce_type: str) -> str:
        advice = {
            "hard_bounce": "Remove this recipient from the list. Email does not exist.",
            "mailbox_full": "Retry later. The recipient's mailbox is full.",
            "spam_rejection": "Your email content or domain may be flagged. Review your templates.",
            "temporary": "Retry with backoff. The server is temporarily unavailable.",
            "greylisting": "Retry later. The server is greylisting unfamiliar senders.",
            "authentication": "Check SMTP credentials. The password may have expired.",
            "rate_limited": "Slow down. You are sending too fast for this provider.",
            "dns": "Check the recipient's domain. It may not exist.",
            "timeout": "Check your network connection. Increase timeout if needed.",
            "tls": "TLS handshake failed. Check SSL certificates.",
            "permanent": "Permanent failure. Review the error message manually.",
            "unknown": "Unclassified error. Check the full SMTP log.",
        }
        return advice.get(bounce_type, "No specific advice available.")
