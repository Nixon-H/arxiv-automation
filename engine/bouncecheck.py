"""Pre-send bounce/deliverability checking via Tor SOCKS.

Filters recipient addresses (VALID / INVALID / UNKNOWN / CATCHALL) before
any real email is sent, so the sending IP is never wasted on dead boxes.

Method per domain:
  1. MX records via Google DoH (plain HTTPS, before any socket patching).
  2. Catch-all probe: RCPT a random non-existent local part; a 250 means the
     domain accepts everything (catch-all) -> all its addresses are UNKNOWN.
  3. Per-address RCPT probe against each MX over Tor SOCKS5:
       250 = VALID, 550 = INVALID, 554/refused/temp = UNKNOWN.

Only the SMTP socket layer goes through Tor; DNS/HTTPS use the normal path.
"""

import json
import random
import socket
import string
import subprocess
import time
import urllib.request

import socks

TOR_SOCKS_HOST = "127.0.0.1"
TOR_SOCKS_PORT = 9050
SMTP_PORT = 25
TIMEOUT = 20
_ORIGINAL_CREATE = socket.create_connection


def get_mx(domain: str) -> list[str]:
    """Return MX hosts (lowest priority first) via Google DoH."""
    url = f"https://dns.google/resolve?name={domain}&type=MX"
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.load(r)
    answers = [a for a in data.get("Answer", []) if a.get("type") == 15]
    mx = sorted((int(a["data"].split()[0]), a["data"].split()[1]) for a in answers)
    return [host.rstrip(".") for _, host in mx]


def _socks_create(*args, **kwargs):
    if args and isinstance(args[0], tuple):
        host, port = args[0][0], args[0][1]
    else:
        host, port = args[0], args[1]
    s = socks.socksocket()
    s.set_proxy(socks.SOCKS5, TOR_SOCKS_HOST, TOR_SOCKS_PORT)
    s.settimeout(kwargs.get("timeout") or TIMEOUT)
    s.connect((host, port))
    return s


def tor_is_up() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        s.connect((TOR_SOCKS_HOST, TOR_SOCKS_PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


def bootstrap_tor() -> bool:
    """Start the Tor service/daemon if the SOCKS port is not listening."""
    if tor_is_up():
        return True
    attempts = [["systemctl", "start", "tor"], ["service", "tor", "start"], ["tor", "--quiet"]]
    for cmd in attempts:
        try:
            subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        for _ in range(20):
            time.sleep(1)
            if tor_is_up():
                return True
    return tor_is_up()


def _rcpt(mx_host: str, sender: str, recipient: str) -> tuple[int, str] | None:
    import smtplib

    socket.create_connection = _socks_create
    try:
        s = smtplib.SMTP(mx_host, SMTP_PORT, timeout=TIMEOUT)
        s.ehlo("gmail.com")
        s.mail(sender)
        code, msg = s.rcpt(recipient)
        try:
            s.quit()
        except Exception:
            pass
        return code, msg.decode("utf-8", "replace") if isinstance(msg, bytes) else str(msg)
    except smtplib.SMTPServerDisconnected:
        return None
    except smtplib.SMTPConnectError:
        return None
    except smtplib.SMTPResponseException as e:
        return e.smtp_code, str(e.smtp_error)
    except OSError:
        return None
    except Exception:
        return None
    finally:
        socket.create_connection = _ORIGINAL_CREATE


def _random_local() -> str:
    return "".join(random.choices(string.ascii_lowercase, k=12))


def check_email(email: str) -> tuple[str, str]:
    """Return (verdict, detail). Verdicts: VALID/INVALID/UNKNOWN/CATCHALL."""
    domain = email.rsplit("@", 1)[1]
    if not bootstrap_tor():
        return "UNKNOWN", "tor unavailable"
    mxs = get_mx(domain)
    if not mxs:
        return "INVALID", "no MX records for domain"
    sender = "bouncecheck@example.com"
    catchall_result = _rcpt(mxs[0], sender, f"{_random_local()}@{domain}")
    catchall_code = catchall_result[0] if catchall_result else None
    if catchall_code == 250:
        return "CATCHALL", f"domain accepts any local part ({mxs[0]})"
    for mx in mxs:
        result = _rcpt(mx, sender, email)
        if result is None:
            continue
        code, detail = result
        if code == 250:
            return "VALID", f"mailbox exists ({mx})"
        if code >= 500:
            low = detail.lower()
            if "blocked" in low or "spamhaus" in low or "rejected" in low or "5.7.1" in low or "policy" in low or "reputation" in low:
                return "UNKNOWN", f"{code} {detail.strip()[:80]} (IP reputation — retry via different exit)"
            return "INVALID", f"{code} {detail.strip()[:80]}"
    return "UNKNOWN", "server refused probe or temporary failure"
