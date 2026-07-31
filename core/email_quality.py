import os
import re
from typing import Any

from core.logger import AppLogger

TEMPLATE_LINT_RULES = [
    (r"  +", "multiple spaces"),
    (r"\n\n\n+", "empty paragraphs"),
    (r"\n\n$", "trailing blank lines"),
    (r"^\s*\n", "leading blank line"),
]


_GREETING_PATTERNS = [
    r"\bdear\b", r"\bhello\b", r"\bhi\b", r"\bgreetings\b",
    r"\bto\b.*\bwhom", r"\bdear\s+(dr|prof|mr|ms|mrs)\b",
    r"\brespected\b",
]


_LONG_LINE_THRESHOLD = 120


SPAM_TRIGGER_WORDS = [
    "act now", "limited time", "click here", "free", "guaranteed",
    "congratulations", "urgent", "winner", "cash", "earn money",
    "work from home", "no obligation", "amazing", "unlimited",
    "exclusive deal", "order now", "trial", "buy now", "discount",
    "bargain", "cheap", "double your", "instant", "once in a lifetime",
    "promise you", "risk free", "satisfaction guaranteed", "stop",
    "bonus", "credit", "debt", "income", "investment", "limited",
    "miracle", "offer", "opt in", "pre approved", "refund", "save",
    "solution", "subscribe", "thousands of", "your income",
]

SPAM_TRIGGER_REGEX = re.compile(
    r"|".join(re.escape(w) for w in SPAM_TRIGGER_WORDS),
    re.IGNORECASE,
)

URL_REGEX = re.compile(r"https?://[^\s<>\"']+|www\.[^\s<>\"']+", re.IGNORECASE)


def check_spam_triggers(text: str) -> list[str]:
    if not text:
        return []
    return sorted(set(SPAM_TRIGGER_REGEX.findall(text)))


def check_spam_score(text: str) -> tuple[int, list[str]]:
    triggers = check_spam_triggers(text)
    return len(triggers), triggers


def check_missing_subject(subject: str | None) -> str | None:
    if not subject or not subject.strip():
        return "Missing subject line"
    if len(subject.strip()) < 5:
        return "Subject too short"
    return None


def check_empty_body(text_body: str | None, html_body: str | None = None) -> str | None:
    if text_body and text_body.strip():
        return None
    if html_body and html_body.strip():
        return None
    return "Empty email body"


def check_broken_links(text: str) -> list[str]:
    urls = URL_REGEX.findall(text)
    broken: list[str] = []
    for url in urls:
        if url.startswith("http") and not url.startswith("https"):
            broken.append(f"Insecure URL (no HTTPS): {url}")
        if "example.com" in url or "domain.com" in url:
            broken.append(f"Placeholder URL: {url}")
    return broken


def lint_template(text: str, template_name: str = "template") -> list[str]:
    issues: list[str] = []
    lines = text.split("\n")

    has_greeting = any(re.search(p, text, re.IGNORECASE) for p in _GREETING_PATTERNS)
    if not has_greeting:
        issues.append("No greeting found (dear/hello/hi)")

    long_lines = sum(1 for line in lines if len(line.strip()) > _LONG_LINE_THRESHOLD)
    if long_lines:
        issues.append(f"{long_lines} line(s) exceed {_LONG_LINE_THRESHOLD} characters")

    for pattern, desc in TEMPLATE_LINT_RULES:
        matches = re.findall(pattern, text)
        if matches:
            issues.append(f"{desc} ({len(matches)} occurrence(s))")

    lines = [line.strip() for line in text.strip().split("\n") if line.strip()]
    for line in reversed(lines):
        if line.endswith((".", "!", "?")):
            break
    else:
        issues.append("Text does not end with sentence-ending punctuation")

    return issues


def check_html_sanity(html: str) -> list[str]:
    issues: list[str] = []
    tags_to_check = ["html", "head", "body", "table", "div", "p", "ul", "ol", "span", "a", "h1", "h2", "h3"]
    for tag in tags_to_check:
        open_count = len(re.findall(rf"<{tag}\b[^>]*>", html, re.IGNORECASE))
        close_count = len(re.findall(rf"</{tag}>", html, re.IGNORECASE))
        if open_count != close_count:
            issues.append(f"<{tag}>: {open_count} opening, {close_count} closing")
    return issues


def compute_email_score(
    subject: str,
    text_body: str,
    html_body: str | None = None,
    has_attachment: bool = False,
    spam_count: int = 0,
    broken_link_count: int = 0,
    lint_issues: list[str] | None = None,
    html_issues: list[str] | None = None,
) -> dict[str, Any]:
    score = 100
    deductions: list[str] = []

    if not subject or len(subject.strip()) < 5:
        score -= 20
        deductions.append("Missing/invalid subject (-20)")
    elif len(subject) > 120:
        score -= 5
        deductions.append("Long subject (-5)")

    if not text_body or not text_body.strip():
        score -= 20
        deductions.append("Empty body (-20)")

    spam_penalty = min(spam_count * 5, 25)
    if spam_penalty:
        score -= spam_penalty
        deductions.append(f"Spam triggers ({spam_count} × -5 = -{spam_penalty})")

    broken_penalty = min(broken_link_count * 8, 24)
    if broken_penalty:
        score -= broken_penalty
        deductions.append(f"Broken links ({broken_link_count} × -8 = -{broken_penalty})")

    lint_count = len(lint_issues or [])
    lint_penalty = min(lint_count * 4, 16)
    if lint_penalty:
        score -= lint_penalty
        deductions.append(f"Template issues ({lint_count} × -4 = -{lint_penalty})")

    html_issue_count = len(html_issues or [])
    html_penalty = min(html_issue_count * 3, 12)
    if html_penalty:
        score -= html_penalty
        deductions.append(f"HTML issues ({html_issue_count} × -3 = -{html_penalty})")

    score = max(0, score)

    grade = "A"
    if score >= 90:
        grade = "A"
    elif score >= 80:
        grade = "B"
    elif score >= 60:
        grade = "C"
    elif score >= 40:
        grade = "D"
    else:
        grade = "F"

    return {
        "score": score,
        "grade": grade,
        "deductions": deductions,
        "deduction_count": len(deductions),
    }


def check_attachment_valid(pdf_path: str) -> bool:
    if not os.path.exists(pdf_path):
        return False
    with open(pdf_path, "rb") as f:
        header = f.read(5)
    if header != b"%PDF-":
        AppLogger.warn(f"Attachment is not a valid PDF: {pdf_path}")
        return False
    return True


def run_email_quality_checks(
    subject: str,
    text_body: str,
    html_body: str | None = None,
    pdf_path: str | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    spam_count, spam_found = check_spam_score(subject + " " + text_body)

    missing_subj = check_missing_subject(subject)
    if missing_subj:
        warnings.append(missing_subj)

    empty_body = check_empty_body(text_body, html_body)
    if empty_body:
        warnings.append(empty_body)

    broken_links = check_broken_links(text_body)
    if broken_links:
        warnings.extend(broken_links)

    if pdf_path and os.path.exists(pdf_path) and not check_attachment_valid(pdf_path):
        warnings.append(f"Attachment invalid: {pdf_path}")

    lint_issues = lint_template(text_body, "text template")
    warnings.extend(f"Lint: {x}" for x in lint_issues)

    html_issues = check_html_sanity(html_body or "")
    warnings.extend(f"HTML: {x}" for x in html_issues)

    score_result = compute_email_score(
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        spam_count=spam_count,
        broken_link_count=len(broken_links),
        lint_issues=lint_issues,
        html_issues=html_issues,
    )

    return {
        "spam_triggers": spam_found,
        "spam_count": spam_count,
        "warnings": warnings,
        "warnings_count": len(warnings),
        "score": score_result["score"],
        "score_grade": score_result["grade"],
        "score_deductions": score_result["deductions"],
    }
