import concurrent.futures
import socket

from core.logger import AppLogger

_DISPOSABLE_DOMAINS: set = set()


def _load_disposable_domains() -> set:
    if _DISPOSABLE_DOMAINS:
        return _DISPOSABLE_DOMAINS
    domains = {
        "mailinator.com", "guerrillamail.com", "tempmail.com", "10minutemail.com",
        "throwaway.email", "yopmail.com", "sharklasers.com", "trashmail.com",
        "mailnator.com", "temp-mail.org", "fakeinbox.com", "mailexpire.com",
        "dispostable.com", "spambox.us", "spambox.info", "spamgourmet.com",
        "getairmail.com", "getonemail.com", "sogetthis.com", "mailmetrash.com",
        "thankyou2010.com", "trash2009.com", "mt2009.com", "trashymail.com",
        "tyldd.com", "uggsrock.com", "wegwerfmail.de", "wh4f.org",
        "whyspam.me", "willselfdestruct.com", "winemaven.info", "wronghead.com",
        "wuzup.net", "xagloo.com", "xemaps.com", "xents.com", "xmaily.com",
        "xoxy.net", "yep.it", "yogamaven.com", "yopmail.fr", "yopmail.net",
        "ypmail.webarnak.fr.eu.org", "yuurok.com", "zehnminutenmail.de",
        "zippymail.info", "zoaxe.com", "zoemail.org", "spamdecoy.net",
        "maileater.com", "mailexpire.com", "mailnull.com", "mailsac.com",
    }
    _DISPOSABLE_DOMAINS.update(domains)
    return _DISPOSABLE_DOMAINS


def check_mx_record(domain: str) -> tuple[bool, str]:
    try:
        answers = socket.getaddrinfo(domain, 25, socket.AF_INET, socket.SOCK_STREAM)
        return True, f"MX resolvable ({len(answers)} record(s))"
    except socket.gaierror:
        try:
            import dns.resolver
            try:
                mx = dns.resolver.resolve(domain, "MX")
                return True, f"MX: {len(mx)} record(s)"
            except Exception:
                return False, f"No MX records for {domain}"
        except ImportError:
            return False, f"Cannot resolve {domain} (no dns.resolver available)"


def check_a_record(domain: str) -> tuple[bool, str]:
    try:
        socket.getaddrinfo(domain, 80, socket.AF_INET, socket.SOCK_STREAM)
        return True, "A record resolvable"
    except socket.gaierror:
        return False, f"No A record for {domain}"


def _resolve_txt(domain: str, prefix: str = "") -> tuple[bool, str]:
    qname = f"{prefix}.{domain}" if prefix else domain
    try:
        import dns.resolver
        try:
            answers = dns.resolver.resolve(qname, "TXT")
            txt = " ".join(a.to_text().strip('"') for a in answers)
            return True, txt[:200]
        except dns.resolver.NoAnswer:
            return False, "No TXT record"
        except dns.resolver.NXDOMAIN:
            return False, "Domain not found"
        except Exception as e:
            return False, str(e)[:60]
    except ImportError:
        import subprocess
        try:
            result = subprocess.run(
                ["dig", "+short", "TXT", qname],
                capture_output=True, text=True, timeout=5,
            )
            out = result.stdout.strip().strip('"')
            if out:
                return True, out[:200]
            return False, "No TXT record via dig"
        except FileNotFoundError:
            return False, "No dns.resolver or dig available"


def check_spf(domain: str) -> tuple[bool, str]:
    ok, txt = _resolve_txt(domain)
    if ok and "v=spf1" in txt:
        return True, f"PASS ({txt[:80]})"
    if ok:
        return False, f"No SPF record (got: {txt[:60]})"
    return False, "SPF: " + txt


def check_dkim(domain: str, selector: str = "default") -> tuple[bool, str]:
    ok, txt = _resolve_txt(domain, f"{selector}._domainkey")
    if ok and "v=DKIM1" in txt:
        return True, f"PASS (selector: {selector})"
    if ok:
        return True, f"Found (non-DKIM1 TXT: {txt[:60]})"
    return False, "DKIM: " + txt


def check_dmarc(domain: str) -> tuple[bool, str]:
    ok, txt = _resolve_txt(domain, "_dmarc")
    if ok and "v=DMARC1" in txt:
        return True, f"PASS ({txt[:80]})"
    if ok:
        return True, f"Found (non-DMARC1 TXT: {txt[:60]})"
    return True, "Not published (common)"


def check_email_auth(domain: str) -> None:
    spf_ok, spf_msg = check_spf(domain)
    dkim_ok, dkim_msg = check_dkim(domain)
    dmarc_ok, dmarc_msg = check_dmarc(domain)
    AppLogger.info(f"SPF:   {'✓' if spf_ok else '✗'} {spf_msg}")
    AppLogger.info(f"DKIM:  {'✓' if dkim_ok else '✗'} {dkim_msg}")
    AppLogger.info(f"DMARC: {'✓' if dmarc_ok else '✗'} {dmarc_msg}")


def is_disposable_email(email: str) -> bool:
    domain = email.split("@")[-1].lower()
    return domain in _load_disposable_domains()


def validate_emails_parallel(emails: list[str], max_workers: int = 10) -> dict[str, tuple[bool, str]]:
    results: dict[str, tuple[bool, str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        fut = {ex.submit(validate_email_dns, e): e for e in emails}
        for f in concurrent.futures.as_completed(fut):
            email = fut[f]
            try:
                results[email] = f.result()
            except Exception as exc:
                results[email] = (False, str(exc))
    return results


def validate_email_dns(email: str) -> tuple[bool, str]:
    domain = email.split("@")[-1].lower()

    if is_disposable_email(email):
        return False, f"Disposable email domain: {domain}"

    mx_ok, mx_msg = check_mx_record(domain)
    if not mx_ok:
        a_ok, a_msg = check_a_record(domain)
        if not a_ok:
            return False, f"Domain {domain} has no MX or A records"

    return True, "DNS valid"
