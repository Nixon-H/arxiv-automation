import os
import time
import random
import argparse
import webbrowser
import email.policy
from email.message import EmailMessage
from datetime import datetime
from typing import List, Dict, Any, Optional

from core.logger import AppLogger
from core.config import AppConfig
from core.database import Database
from core.ratelimiter import RateLimiter
from core.lock import FileLock
from core.doctor import Doctor
from core.metrics import MetricsRegistry, start_metrics_server
from core.notifications import Notifier
from core.i18n import Translator
from core.validator import (
    validate_email_format,
    generate_sha256,
    verify_file_integrity,
    validate_pdf,
    pre_flight_checks,
)
from core.exceptions import SafetyLockoutError, FileLockError
from core.email_quality import run_email_quality_checks
from parsing.parser import DataParser, DuplicateStats
from parsing.bounce import BounceClassifier
from engine.templates import TemplateEngine
from engine.smtp import SmtpEngine
from engine.plugins import PluginManager
from exports.report import ReportExporter


class OrchestrationRunner:
    def __init__(self, cli_args: argparse.Namespace) -> None:
        self.args = cli_args
        self.config = AppConfig()
        self.db = Database.get_instance()
        self.lock = FileLock()
        self.rate_limiter = RateLimiter(self.db)
        self.templates = TemplateEngine()
        self.plugins = PluginManager()
        self.i18n = Translator(locale=cli_args.locale if hasattr(cli_args, 'locale') and cli_args.locale else 'en')
        self.notifier = Notifier(self.config.notifications)
        self.metrics = MetricsRegistry(self.db)
        self.bounce_classifier = BounceClassifier(self.db)

        self.limits = self.config.safety_limits
        self.identity = self.config.sender_identity
        accounts_raw = self.config.accounts

        from core.config_typed import SMTPAccount
        self.typed_accounts = [
            SMTPAccount(
                provider=a["provider"],
                email=a["email"],
                password=a["password"],
                server=a["server"],
                port=a["port"],
                display_name=a.get("display_name", "Research Outreach"),
                max_per_hour=a.get("max_per_hour", 20),
                max_per_day=a.get("max_per_day", 200),
            )
            for a in accounts_raw
        ]

        self.smtp_engine = SmtpEngine(
            accounts=self.typed_accounts,
            db=self.db,
            rate_limiter=self.rate_limiter,
            timeout=float(self.limits.get("smtp_timeout_seconds", 30.0)),
        )

        self.stats = {
            "sent": 0,
            "failed": 0,
            "skipped": 0,
            "duplicates": 0,
            "retries": 0,
            "start_time": time.time(),
            "latencies": [],
        }

    def process_execution_pipeline(self) -> None:
        AppLogger.info("Starting execution pipeline...")

        if self.args.verify:
            self.run_pre_flight_checks()
            return

        if self.args.reset_progress:
            self.db.set_progress_index(0)
            AppLogger.success("Progress reset.")
            return

        if self.args.stats:
            self.print_stats()
            return

        if self.args.export_report:
            ReportExporter().export_all(self.stats, db=self.db)
            return

        if self.args.retry_failed:
            AppLogger.info("Retry-failed: rerun with --live to process next pending record.")
            return

        if self.args.validate_config:
            self.validate_config()
            return

        if self.args.doctor is not None:
            if self.args.doctor == "bundle":
                from core.doctor import Doctor
                Doctor.create_bundle()
                return
            self.run_doctor()
            return

        if self.args.scheduler:
            self.generate_scheduler()
            return

        if self.args.metrics:
            port = int(self.args.metrics)
            server = start_metrics_server(self.metrics, port)
            AppLogger.info(f"Metrics endpoint at http://127.0.0.1:{port}/metrics")
            if not self.args.live and not self.args.test and not self.args.dry_run:
                AppLogger.info("Metrics server running. Press Ctrl+C to stop.")
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    server.shutdown()
                return

        # Acquire file lock for live operations
        if self.args.live:
            if not self.lock.acquire(timeout=1):
                pid = FileLock.get_locked_pid()
                raise FileLockError(
                    f"Another instance is running (PID {pid}). Use '--reset-progress' if stuck."
                )

        # Identify input file
        input_file = None
        for candidate in ["endorsers.txt", "endorsers.csv", "endorsers.json", "endorsers.yaml", "endorsers.xlsx"]:
            if os.path.exists(candidate):
                input_file = candidate
                break

        if not input_file:
            AppLogger.error("No endorser data file found.")
            return

        records: List[Dict[str, str]]
        dup_stats: DuplicateStats
        records, dup_stats = DataParser.auto_detect_with_stats(input_file)
        if not records:
            AppLogger.error("No valid records parsed.")
            return

        AppLogger.info(f"Loaded {len(records)} endorser records from {input_file}")
        if dup_stats.total_duplicates:
            self.stats["duplicates_in_file"] = dup_stats.total_duplicates
        self.metrics.gauge_set("records_loaded", len(records))

        # Register recipients in DB
        for rec in records:
            email_hash = generate_sha256(rec["email"])
            self.db.upsert_recipient(rec["last_name"], rec["email"], rec["paper_title"], email_hash)

        if self.args.dry_run:
            idx = self.db.get_progress_index()
            if idx < len(records):
                self.dry_run_single(records, idx)
                self.generate_preview_html(records)
            else:
                AppLogger.success(f"All {len(records)} records processed.")
            return

        if self.args.test:
            self.run_test_mode(records)
            return

        # Live mode
        idx = self.db.get_progress_index()
        if idx >= len(records):
            AppLogger.success(f"All {len(records)} records processed.")
            self.print_stats()
            self.notifier.send_completion(self.stats)
            return

        # Cooldown check
        last_send = self.db.get_last_send()
        cooldown = float(self.limits.get("cooldown_hours", 24.0))
        if last_send and last_send["timestamp"]:
            import datetime
            if hasattr(last_send["timestamp"], "timestamp"):
                last_ts = last_send["timestamp"].timestamp()
            else:
                from datetime import datetime as dt
                last_ts = dt.fromisoformat(str(last_send["timestamp"])).timestamp()
            elapsed = time.time() - last_ts
            if elapsed < cooldown * 3600:
                remaining = (cooldown * 3600 - elapsed) / 3600
                AppLogger.error(f"Cooldown active — {remaining:.2f}h left.")
                self.lock.release()
                return

        # Resume prompt
        if idx > 0:
            total_sent = self.stats["sent"]
            print(f"  Progress: {idx}/{len(records)} records processed ({total_sent} sent)")
            print(f"  Resume from #{idx + 1}? [Enter=yes, n=restart from 0]: ", end="", flush=True)
            try:
                resp = input().strip().lower()
                if resp == "n":
                    self.db.set_progress_index(0)
                    idx = 0
                    self.stats["sent"] = 0
                    self.stats["failed"] = 0
                    AppLogger.info("Restarting from record #0")
            except (EOFError, KeyboardInterrupt):
                pass

        send_n = self.args.send_n or 1
        end = min(idx + send_n, len(records))
        total_to_send = end - idx
        success_count = 0
        progress_width = 30

        def _render_progress(done: int, total: int) -> str:
            filled = int(progress_width * done / total) if total else 0
            bar = "█" * filled + "░" * (progress_width - filled)
            return f"[{bar}] {done}/{total}"

        for i in range(idx, end):
            progress_bar = _render_progress(i - idx, total_to_send)
            AppLogger.info(f"Progress {progress_bar} — sending #{i + 1}")
            ok = self.live_send(records, i)
            if ok:
                success_count += 1
                self.db.set_progress_index(i + 1)
                self.archive_sent_email(records[i])
                # Delay between sends
                d_range = self.limits.get("random_delay_range_seconds", [5, 15])
                delay = random.uniform(d_range[0], d_range[1])
                AppLogger.info(f"Delay {delay:.1f}s before next...")
                time.sleep(delay)
            else:
                self.db.set_progress_index(i)
                break

        self.print_stats()
        self.notifier.send_completion(self.stats)
        self.lock.release()

    def dry_run_single(self, records: List[Dict[str, str]], idx: int) -> None:
        target = records[idx]
        context = {
            "last_name": target["last_name"],
            "paper_title": target["paper_title"],
            "your_name": self.identity["your_name"],
            "your_paper_title": self.identity["your_paper_title"],
            "arxiv_category": self.identity["arxiv_category"],
        }
        rendered = self.templates.render_all(context)
        AppLogger.info(f"--- DRY RUN: Record #{idx} ---")
        print(f"  To: {target['last_name']} <{target['email']}>")
        print(f"  Subject: {rendered['subject']}")
        print(f"  Paper: {target['paper_title']}")
        qc = run_email_quality_checks(rendered["subject"], rendered["text_body"], rendered["html_body"], "my_paper.pdf")
        if qc["warnings"]:
            for w in qc["warnings"]:
                print(f"  ⚠ {w}")
        if qc["spam_count"] > 3:
            print(f"  ⚠ Spam triggers ({qc['spam_count']}): {qc['spam_triggers']}")
        score = qc.get("score", 100)
        grade = qc.get("score_grade", "A")
        print(f"  Email Score         : {score}/100 ({grade})")
        deductions = qc.get("score_deductions", [])
        for d in deductions:
            print(f"    - {d}")
        if self.plugins.plugin_count:
                print(f"  Plugins: {self.plugins.plugin_count} active")

    def run_test_mode(self, records: List[Dict[str, str]]) -> None:
        target_outbox = self.args.test if "@" in self.args.test else "test@example.com"
        AppLogger.info(f"Test mode — sending to {target_outbox}")

        context = {
            "last_name": "TestUser",
            "paper_title": "Test Paper Title",
            "your_name": self.identity["your_name"],
            "your_paper_title": self.identity["your_paper_title"],
            "arxiv_category": self.identity["arxiv_category"],
        }
        rendered = self.templates.render_all(context)
        pdf_path = "my_paper.pdf"

        acct_idx = 0
        if self.typed_accounts:
            acct_idx = 0

        context["recipient"] = target_outbox
        self.plugins.run_before_send(context)

        success, err_class, latency_ms, err_msg = self.smtp_engine.send_atomic(
            account_idx=acct_idx,
            recipient_email=target_outbox,
            subject=f"[TEST] {rendered['subject']}",
            text_content=rendered['text_body'],
            html_content=rendered['html_body'],
            attachment_path=pdf_path if os.path.exists(pdf_path) else None,
        )

        result = {"success": success, "error": err_class.value, "latency_ms": latency_ms}
        self.plugins.run_after_send(context, result)

        if success:
            AppLogger.success(f"Test email sent.")
            self.stats["sent"] += 1
        else:
            AppLogger.error(f"Test failed: {err_class.value}: {err_msg}")

    def live_send(self, records: List[Dict[str, str]], idx: int) -> bool:
        target = records[idx]
        email = target["email"]
        email_hash = generate_sha256(email)

        if not validate_email_format(email):
            AppLogger.warn(f"Invalid email at {idx}: '{email}'")
            self.stats["skipped"] += 1
            self.db.set_progress_index(idx + 1)
            return True

        if self.db.is_email_sent(email_hash):
            AppLogger.warn(f"Duplicate at {idx}: {email}")
            self.stats["duplicates"] += 1
            self.db.set_progress_index(idx + 1)
            return True

        recipient_id = self.db.upsert_recipient(target["last_name"], email, target["paper_title"], email_hash)

        context = {
            "last_name": target["last_name"],
            "paper_title": target["paper_title"],
            "your_name": self.identity["your_name"],
            "your_paper_title": self.identity["your_paper_title"],
            "arxiv_category": self.identity["arxiv_category"],
            "recipient": email,
            "recipient_id": recipient_id,
        }

        self.plugins.run_before_send(context)
        rendered = self.templates.render_all(context)
        pdf_path = "my_paper.pdf"

        # Render validation
        missing_vars = self.templates.validate_context(context)
        if missing_vars:
            AppLogger.warn(f"Template missing variables: {missing_vars}")

        qc = run_email_quality_checks(rendered["subject"], rendered["text_body"], rendered["html_body"], pdf_path)
        for w in qc["warnings"]:
            AppLogger.warn(f"Quality: {w}")
        if qc["spam_count"] > 3:
            AppLogger.warn(f"Spam triggers ({qc['spam_count']}): {qc['spam_triggers']}")
        if qc["warnings_count"] > 0:
            AppLogger.warn(f"Quality warnings: {qc['warnings_count']} issue(s)")
        score = qc.get("score", 100)
        grade = qc.get("score_grade", "A")
        AppLogger.info(f"Email score: {score}/100 ({grade})")

        correlation_id = __import__("uuid").uuid4().hex[:12]
        start_time = time.time()
        success = self.smtp_engine.send_with_adaptive_routing(
            recipient_email=email,
            subject=rendered["subject"],
            text_content=rendered["text_body"],
            html_content=rendered["html_body"],
            recipient_id=recipient_id,
            attachment_path=pdf_path if os.path.exists(pdf_path) else None,
            correlation_id=correlation_id,
        )

        latency = time.time() - start_time
        result = {"success": success, "latency": latency}
        self.plugins.run_after_send(context, result)

        if success:
            self.stats["sent"] += 1
            self.stats["latencies"].append(latency)
            self.metrics.counter_inc("emails_sent")
            self.metrics.histogram_observe("smtp_latency", latency)
            return True
        else:
            self.stats["failed"] += 1
            self.metrics.counter_inc("emails_failed")
            return False

    def run_pre_flight_checks(self) -> bool:
        AppLogger.info("Running pre-flight checks...")
        checks = []

        pdf_ok, pdf_msg = validate_pdf("my_paper.pdf") if os.path.exists("my_paper.pdf") else (False, "No PDF")
        checks.append(("PDF Attachment", pdf_ok, pdf_msg))
        checks.append(("Text Template", os.path.exists("template.txt"), "OK" if os.path.exists("template.txt") else "Missing"))
        checks.append(("HTML Template", os.path.exists("template.html"), "OK" if os.path.exists("template.html") else "Missing"))
        checks.append(("Data File", any(os.path.exists(f) for f in ["endorsers.txt", "endorsers.csv"]), "OK"))

        for idx, acct in enumerate(self.typed_accounts):
            pw_ok = len(acct.password) > 4
            checks.append((f"Account #{idx} ({acct.email})", pw_ok, "OK" if pw_ok else "Check password"))

        return pre_flight_checks(checks)

    def run_doctor(self) -> None:
        doc = Doctor()

        doc.add(doc.check_python_version())
        doc.add(doc.check_file_exists("config.json"))
        doc.add(doc.check_file_exists("template.txt", "Text Template"))
        doc.add(doc.check_file_exists("template.html", "HTML Template"))
        doc.add(doc.check_file_exists(".env", "Env File"))
        doc.add(doc.check_writable("data/"))
        doc.add(doc.check_writable("logs/"))

        for acct in self.typed_accounts:
            doc.add(doc.check_file_exists(acct.email.replace("@", "_at_"), f"Account {acct.email}"))
            doc.add(lambda a=acct: ("SMTP " + a.email, True, a.server + ":" + str(a.port)))

        doc.add(doc.check_import("json"))
        doc.add(doc.check_import("csv"))
        doc.add(("SQLite DB", True, "Connected"))

        if os.path.exists("my_paper.pdf"):
            doc.add(doc.check_file_exists("my_paper.pdf", "PDF Attachment"))

        doc.add(("File Lock", not self.lock.is_locked(), "Not locked" if not self.lock.is_locked() else "Locked by " + str(FileLock.get_locked_pid())))

        for acct in self.typed_accounts[:1]:
            domain = acct.email.split("@")[-1]
            doc.add(doc.check_email_auth(domain))

        if os.path.exists("my_paper.pdf"):
            doc.add(doc.check_attachment("my_paper.pdf"))
        doc.add(doc.check_template_diff("template.txt"))
        doc.add(doc.check_template_diff("template.html"))

        doc.run_all()

    def generate_scheduler(self) -> None:
        import textwrap
        script_path = os.path.abspath("run.py")
        work_dir = os.path.dirname(script_path)

        cron_line = f"0 */6 * * * cd {work_dir} && python3 {script_path} --live"

        systemd_service = textwrap.dedent(f"""
        [Unit]
        Description=arXiv Endorsement Dispatch
        After=network.target

        [Service]
        Type=oneshot
        WorkingDirectory={work_dir}
        ExecStart=python3 {script_path} --live
        User={os.environ.get('USER', 'root')}
        StandardOutput=journal

        [Install]
        WantedBy=multi-user.target
        """).strip()

        systemd_timer = textwrap.dedent("""
        [Unit]
        Description=Run arXiv dispatch every 6 hours

        [Timer]
        OnBootSec=5min
        OnUnitActiveSec=6h
        Unit=arxiv-dispatch.service

        [Install]
        WantedBy=timers.target
        """).strip()

        print("\n=== CRON ===")
        print(cron_line)
        print("\n=== SYSTEMD SERVICE (scheduler/arxiv-dispatch.service) ===")
        print(systemd_service)
        print("\n=== SYSTEMD TIMER (scheduler/arxiv-dispatch.timer) ===")
        print(systemd_timer)

        os.makedirs("scheduler", exist_ok=True)
        with open("scheduler/arxiv-dispatch.service", "w") as f:
            f.write(systemd_service + "\n")
        with open("scheduler/arxiv-dispatch.timer", "w") as f:
            f.write(systemd_timer.strip() + "\n")
        AppLogger.success("Scheduler files written to scheduler/")

    def generate_preview_html(self, records: List[Dict[str, str]]) -> None:
        html_parts = []
        idx = self.db.get_progress_index()
        for i, rec in enumerate(records):
            ctx = {
                "last_name": rec["last_name"],
                "paper_title": rec["paper_title"],
                "your_name": self.identity["your_name"],
                "your_paper_title": self.identity["your_paper_title"],
                "arxiv_category": self.identity["arxiv_category"],
            }
            rendered = self.templates.render_all(ctx)
            status = "✓ NEXT" if i == idx else ("✓ DONE" if i < idx else "—")
            html_parts.append(f"""\
        <div class="email-card {'next' if i == idx else 'done' if i < idx else ''}">
          <div class="status-badge">{status}</div>
          <h3>{rec['last_name']}</h3>
          <p class="meta">To: {rec['email']} &mdash; Paper: {rec['paper_title']}</p>
          <p class="meta"><strong>Subject:</strong> {rendered['subject']}</p>
          <div class="body-preview">{rendered['text_body']}</div>
        </div>""")

        preview_html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>arXiv Dispatch — Preview</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font:14px/1.6 system-ui,sans-serif; background:#f5f5f5; color:#222; padding:20px; }}
  h1 {{ font-size:22px; margin-bottom:4px; }}
  .sub {{ color:#666; margin-bottom:20px; }}
  .email-card {{ background:#fff; border-radius:8px; padding:16px 20px; margin-bottom:12px;
                 border-left:4px solid #999; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
  .email-card.next {{ border-left-color:#22c55e; }}
  .email-card.done {{ border-left-color:#94a3b8; opacity:.7; }}
  .status-badge {{ display:inline-block; padding:2px 10px; border-radius:10px;
                   font-size:11px; font-weight:700; background:#e2e8f0; color:#475569;
                   margin-bottom:8px; }}
  .email-card.next .status-badge {{ background:#22c55e; color:#fff; }}
  .meta {{ color:#555; font-size:13px; margin-bottom:6px; }}
  .body-preview {{ background:#f8fafc; border-radius:6px; padding:12px; margin-top:8px;
                   white-space:pre-wrap; font-size:13px; color:#333; max-height:300px; overflow-y:auto; }}
  .summary {{ background:#fff; border-radius:8px; padding:16px 20px; margin-bottom:20px;
              box-shadow:0 1px 3px rgba(0,0,0,.08); }}
  .summary span {{ margin-right:20px; font-size:13px; }}
  .summary strong {{ font-size:18px; }}
</style>
</head>
<body>
<h1>📬 arXiv Dispatch — Preview</h1>
<p class="sub">{len(records)} endorser{'' if len(records) == 1 else 's'} loaded</p>
<div class="summary">
  <span>Total: <strong>{len(records)}</strong></span>
  <span>Pending: <strong>{len(records) - idx}</strong></span>
  <span>Done: <strong>{idx}</strong></span>
  <span>Next: <strong>#{idx + 1}</strong></span>
</div>
{''.join(html_parts)}
</body>
</html>"""
        preview_path = "preview.html"
        with open(preview_path, "w", encoding="utf-8") as f:
            f.write(preview_html)
        abs_path = os.path.abspath(preview_path)
        AppLogger.success(f"Preview written to {abs_path}")
        if not getattr(self.args, "no_browser", False):
            try:
                webbrowser.open(f"file://{abs_path}")
            except Exception:
                pass

    def archive_sent_email(self, record: Dict[str, str]) -> None:
        import email.policy
        from email.message import EmailMessage
        from datetime import datetime

        context = {
            "last_name": record["last_name"],
            "paper_title": record["paper_title"],
            "your_name": self.identity["your_name"],
            "your_paper_title": self.identity["your_paper_title"],
            "arxiv_category": self.identity["arxiv_category"],
        }
        rendered = self.templates.render_all(context)

        msg = EmailMessage()
        msg["From"] = self.identity.get("your_name", "Sender")
        msg["To"] = record["email"]
        msg["Subject"] = rendered["subject"]
        msg["Date"] = datetime.now().strftime("%a, %d %b %Y %H:%M:%S %z")
        msg["X-Mailer"] = "ArxivDispatch/5.0"
        msg.set_content(rendered["text_body"])
        if rendered.get("html_body"):
            msg.add_alternative(rendered["html_body"], subtype="html")

        archive_dir = "sent"
        os.makedirs(archive_dir, exist_ok=True)
        safe_name = record["last_name"].replace(" ", "_").replace("/", "_")[:40]
        fname = f"{datetime.now().strftime('%Y-%m-%d')}_{safe_name}.eml"
        fpath = os.path.join(archive_dir, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(msg.as_string())
        AppLogger.info(f"Archived: {fpath}")

    def validate_config(self) -> None:
        AppLogger.info("Configuration validation:")
        print(f"  Sender: {self.identity.get('your_name', 'N/A')}")
        print(f"  Paper: {self.identity.get('your_paper_title', 'N/A')}")
        print(f"  Category: {self.identity.get('arxiv_category', 'N/A')}")
        print(f"  Accounts: {len(self.typed_accounts)}")
        for a in self.typed_accounts:
            health = self.db.fetchone("SELECT * FROM accounts WHERE email = ?", (a.email,))
            status = "healthy"
            if health:
                if health["suspended_until"] and time.time() < health["suspended_until"]:
                    status = "suspended"
                if health["auth_failures"] >= 3:
                    status = "auth_locked"
            print(f"    - {a.email} ({a.provider}) [{status}]")
        print(f"  Cooldown: {self.limits.get('cooldown_hours', 24)}h")
        print(f"  Delay: {self.limits.get('random_delay_range_seconds', [5, 15])}")
        print(f"  Retries: {self.limits.get('max_retries', 3)}")
        idx = self.db.get_progress_index()
        print(f"  Progress: {idx}")
        sent_count = self.db.fetchone("SELECT COUNT(*) as c FROM sends WHERE status='success'")
        print(f"  Sent: {sent_count['c'] if sent_count else 0}")
        print("  Configuration: VALID")

    def print_domain_reputation(self) -> None:
        try:
            domains = self.db.get_domain_reputation()
            if not domains:
                return
            print()
            print("  DOMAIN REPUTATION")
            print(f"  {'Domain':25} {'Total':>6} {'Success':>8} {'Rate':>6} {'Avg(ms)':>7}")
            print("  " + "-" * 55)
            for d in domains:
                domain = d.get("domain", "?") or ""
                total = d.get("total", 0)
                success = d.get("success", 0)
                rate = (success / total * 100) if total else 0
                avg = d.get("avg_latency_ms", 0) or 0
                print(f"  {domain[:24]:25} {total:6} {success:8} {rate:5.0f}% {avg:7}ms")
            print()
        except Exception:
            pass

    def print_stats(self) -> None:
        elapsed = time.time() - self.stats.get("start_time", time.time())
        avg_lat = 0.0
        if self.stats["latencies"]:
            avg_lat = sum(self.stats["latencies"]) / len(self.stats["latencies"])
        db_stats = self.db.get_send_stats()
        idx = self.db.get_progress_index()
        accts = self.db.get_all_accounts()

        print("\n" + "=" * 55)
        print("  RUN METRICS SUMMARY")
        print("=" * 55)
        print(f"  Sent               : {self.stats['sent']} (DB: {db_stats.get('sent', 0)})")
        print(f"  Failed             : {self.stats['failed']} (DB: {db_stats.get('failed', 0)})")
        print(f"  Skipped            : {self.stats['skipped']}")
        print(f"  Duplicates         : {self.stats['duplicates']}")
        print(f"  Progress Index     : {idx}")
        print(f"  Elapsed            : {elapsed:.1f}s")
        if self.stats["latencies"]:
            print(f"  Avg Latency        : {avg_lat:.2f}s")
        print(f"  Accounts           : {len(accts)}")
        for a in accts:
            print(f"    {a['email']}: sent={a['sent_total']} fail={a['failures_today']} rate={a['success_rate']:.0%}")
        metrics_snap = self.metrics.snapshot()
        print(f"  Runtime counters   : {metrics_snap.get('counters', {})}")
        print("=" * 55 + "\n")
        self.print_domain_reputation()
