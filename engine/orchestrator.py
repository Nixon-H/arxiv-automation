import argparse
import os
import random
import time
import uuid
import webbrowser
from datetime import datetime
from email.message import EmailMessage
from typing import Any

from core.config import AppConfig
from core.database import Database
from core.doctor import Doctor
from core.email_quality import run_email_quality_checks
from core.exceptions import FileLockError
from core.i18n import Translator
from core.lock import FileLock
from core.logger import AppLogger
from core.metrics import MetricsRegistry, start_metrics_server
from core.notifications import Notifier
from core.ratelimiter import RateLimiter
from core.validator import (
    generate_sha256,
    pre_flight_checks,
    validate_email_format,
    validate_pdf,
)
from engine.plugins import PluginManager
from engine.smtp import SmtpEngine
from engine.templates import TemplateEngine
from exports.report import ReportExporter
from parsing.bounce import BounceClassifier
from parsing.parser import DataParser, DuplicateStats


class OrchestrationRunner:
    def __init__(self, cli_args: argparse.Namespace) -> None:
        self.args = cli_args
        self.config = AppConfig()
        self.db = Database.get_instance()
        self.lock = FileLock()
        self.rate_limiter = RateLimiter(self.db)
        template_name = getattr(cli_args, "template", "") or ""
        if template_name:
            self.templates = TemplateEngine(
                txt_paths=[f"template_{template_name}.txt"],
                html_paths=[f"template_{template_name}.html"],
            )
        else:
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

        self.stats: dict[str, Any] = {
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

        if self.args.followups is not None:
            self.run_followups(int(self.args.followups))
            return

        if self.args.mark_replied:
            self.mark_replied(self.args.mark_replied)
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
        template_name = getattr(self.args, "template", "") or ""
        input_file = None
        contacts_override = getattr(self.args, "contacts", "") or ""
        if self.args.dry_run and contacts_override:
            for candidate in ["endorsers.txt", "endorsers.csv", "endorsers.json", "endorsers.yaml", "endorsers.xlsx"]:
                if os.path.exists(candidate):
                    input_file = candidate
                    break
            if not input_file:
                input_file = contacts_override
        else:
            if contacts_override:
                if os.path.exists(contacts_override):
                    input_file = contacts_override
                else:
                    AppLogger.error(f"Contacts file not found: {contacts_override}")
                    return
            if not input_file:
                for candidate in ["endorsers.txt", "endorsers.csv", "endorsers.json", "endorsers.yaml", "endorsers.xlsx"]:
                    if os.path.exists(candidate):
                        input_file = candidate
                        break

        if not input_file:
            self._create_sample_endorsers()
            AppLogger.info(
                "Created sample endorsers.txt — edit it with real recipients (Name is qualified to endorse. / Paper title / email) and run again."
            )
            return

        records: list[dict[str, str]]
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
            groups = self._build_groups(input_file, records, template_name)
            for g in groups:
                if g["tnum"] == 1:
                    g["idx"] = self.db.get_progress_index()
                else:
                    g["idx"] = int(self.db.get_meta(f"current_index_g{g['tnum']}", "0") or "0")
                if g["idx"] < len(g["records"]):
                    AppLogger.info(f"--- DRY RUN: Template {g['tnum']} ({g['label']}) ---")
                    self.dry_run_single(g["records"], g["idx"], engine=g["engine"])
            self.generate_preview_html(groups)
            return

        if self.args.test:
            self.run_test_mode(records)
            return

        # Live mode — build all dispatch groups (primary + --group extras)
        groups = self._build_groups(input_file, records, template_name)

        # Cooldown check
        last_send = self.db.get_last_send()
        cooldown = float(self.limits.get("cooldown_hours", 24.0))
        if last_send and last_send["timestamp"]:
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

        send_n = self.args.send_n or 1
        success_count = 0
        progress_width = 30

        def _render_progress(done: int, total: int) -> str:
            filled = int(progress_width * done / total) if total else 0
            bar = "█" * filled + "░" * (progress_width - filled)
            return f"[{bar}] {done}/{total}"

        for g in groups:
            g_records = g["records"]
            if g["tnum"] == 1:
                idx = self.db.get_progress_index()
            else:
                idx = int(self.db.get_meta(f"current_index_g{g['tnum']}", "0") or "0")
            g["idx"] = idx

            if idx >= len(g_records):
                AppLogger.success(f"Template {g['tnum']} ({g['label']}): all {len(g_records)} records processed.")
                continue

            # Resume prompt
            if idx > 0:
                total_sent = self.stats["sent"]
                print(f"  Template {g['tnum']} ({g['label']}) — Progress: {idx}/{len(g_records)} records processed ({total_sent} sent)")
                print(f"  Resume from #{idx + 1}? [Enter=yes, n=restart from 0]: ", end="", flush=True)
                try:
                    resp = input().strip().lower()
                    if resp == "n":
                        if g["tnum"] == 1:
                            self.db.set_progress_index(0)
                        else:
                            self.db.set_meta(f"current_index_g{g['tnum']}", "0")
                        idx = 0
                        g["idx"] = 0
                        self.stats["sent"] = 0
                        self.stats["failed"] = 0
                        AppLogger.info("Restarting from record #0")
                except (EOFError, KeyboardInterrupt):
                    pass

            end = min(idx + send_n, len(g_records))
            total_to_send = end - idx
            for i in range(idx, end):
                progress_bar = _render_progress(i - idx, total_to_send)
                AppLogger.info(f"Progress {progress_bar} — T{g['tnum']} sending #{i + 1}")
                ok = self.live_send(g_records, i, engine=g["engine"], gkey=f"g{g['tnum']}" if g["tnum"] != 1 else None)
                if ok:
                    success_count += 1
                    if g["tnum"] == 1:
                        self.db.set_progress_index(i + 1)
                    else:
                        self.db.set_meta(f"current_index_g{g['tnum']}", str(i + 1))
                    self.archive_sent_email(g_records[i])
                    # Delay between sends
                    d_range = self.limits.get("random_delay_range_seconds", [5, 15])
                    delay = random.uniform(d_range[0], d_range[1])
                    AppLogger.info(f"Delay {delay:.1f}s before next...")
                    time.sleep(delay)
                else:
                    if g["tnum"] == 1:
                        self.db.set_progress_index(i)
                    else:
                        self.db.set_meta(f"current_index_g{g['tnum']}", str(i))
                    break

        self.print_stats()
        self.notifier.send_completion(self.stats)
        self.lock.release()

    def _build_groups(self, input_file: str, primary_records: list[dict[str, str]], primary_template: str) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = [
            {
                "tnum": 1,
                "label": primary_template or "default",
                "file": input_file,
                "engine": self.templates,
                "records": primary_records,
                "idx": 0,
            }
        ]
        for spec in getattr(self.args, "group", []) or []:
            if ":" in spec:
                file_part, tpl = spec.rsplit(":", 1)
                file_part = file_part.strip()
                tpl = tpl.strip()
            else:
                file_part, tpl = spec.strip(), ""
            if not file_part or not os.path.exists(file_part):
                AppLogger.error(f"Group file not found: {file_part}")
                continue
            g_records, _ = DataParser.auto_detect_with_stats(file_part)
            if not g_records:
                AppLogger.error(f"No valid records parsed from group file {file_part}")
                continue
            for rec in g_records:
                email_hash = generate_sha256(rec["email"])
                self.db.upsert_recipient(rec["last_name"], rec["email"], rec["paper_title"], email_hash)
            engine = TemplateEngine(
                txt_paths=[f"template_{tpl}.txt"],
                html_paths=[f"template_{tpl}.html"],
            ) if tpl else TemplateEngine()
            tnum = len(groups) + 1
            groups.append(
                {
                    "tnum": tnum,
                    "label": tpl or "default",
                    "file": file_part,
                    "engine": engine,
                    "records": g_records,
                    "idx": 0,
                }
            )
        return groups

    def _create_sample_endorsers(self) -> None:
        sample = (
            "Elena Vasquez is qualified to endorse.\n"
            "Deep Learning for Safety\n"
            "elena.vasquez@example.com\n\n"
            "Marcus Chen is qualified to endorse.\n"
            "Foundations of Machine Learning\n"
            "marcus.chen@example.com\n"
        )
        try:
            with open("endorsers.txt", "w", encoding="utf-8") as f:
                f.write(sample)
        except OSError as e:
            AppLogger.error(f"Could not create endorsers.txt: {e}")

    def run_followups(self, days: int) -> None:
        candidates = self.db.get_followup_candidates(days, max_followups=2)
        if not candidates:
            AppLogger.info(f"No follow-ups due (no-reply recipients, last send > {days}d, followups < 2).")
            return
        AppLogger.info(f"Follow-up candidates ({len(candidates)}):")
        for c in candidates:
            print(f"  {c['last_name']:<28} {c['email']:<40} sends={c['send_count']} followups={c['followup_count']} last={c['last_send_at']}")

        send_n = self.args.send_n or 0
        if send_n <= 0:
            AppLogger.info("Add --send N to send follow-ups to the first N candidates.")
            return
        if not self.typed_accounts:
            AppLogger.error("No SMTP accounts configured.")
            return

        confirm = input(f"Send follow-ups to first {send_n} candidate(s)? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            AppLogger.info("Cancelled.")
            return

        sent = 0
        for c in candidates[:send_n]:
            context = {
                "last_name": c["last_name"],
                "title": "",
                "greeting": c["last_name"],
                "paper_title": c["paper_title"],
                "your_name": self.identity["your_name"],
                "your_paper_title": self.identity["your_paper_title"],
                "arxiv_category": self.identity["arxiv_category"],
                "recipient": c["email"],
            }
            try:
                body = self.templates.render_file("template_followup.txt", context)
            except Exception as e:
                AppLogger.error(f"Follow-up render failed: {e}")
                break
            subject = f"Follow-up: {self.identity['your_paper_title']}"
            corr_id = uuid.uuid4().hex[:12]
            success, err_class, latency_ms, err_msg, _phases = self.smtp_engine.send_atomic(
                account_idx=0,
                recipient_email=c["email"],
                subject=subject,
                text_content=body,
                html_content=body,
                attachment_path=None,
                correlation_id=corr_id,
            )
            if success:
                self.db.increment_followup(c["id"])
                self.db.record_send(
                    c["id"], self.typed_accounts[0].email, "success",
                    latency_ms=latency_ms, correlation_id=corr_id,
                )
                self.stats["sent"] += 1
                sent += 1
                AppLogger.success(f"Follow-up sent to {c['last_name']} ({c['email']})")
                import random as _r
                d_range = self.limits.get("random_delay_range_seconds", [5, 15])
                time.sleep(_r.uniform(d_range[0], d_range[1]))
            else:
                AppLogger.error(f"Follow-up failed for {c['email']}: {err_class.value}: {err_msg}")
        self.print_stats()

    def mark_replied(self, email: str) -> None:
        self.db.mark_replied(email)
        AppLogger.success(f"Marked {email} as replied — no further follow-ups.")

    def _build_greeting(self, target: dict[str, str]) -> str:
        title = target.get("title", "").strip()
        last_name = target["last_name"]
        if title:
            return f"{title} {last_name}"
        return last_name

    def dry_run_single(self, records: list[dict[str, str]], idx: int, engine: TemplateEngine | None = None) -> None:
        engine = engine or self.templates
        target = records[idx]
        context = {
            "last_name": target["last_name"],
            "title": target.get("title", ""),
            "greeting": self._build_greeting(target),
            "paper_title": target["paper_title"],
            "your_name": self.identity["your_name"],
            "your_paper_title": self.identity["your_paper_title"],
            "arxiv_category": self.identity["arxiv_category"],
        }
        rendered = engine.render_all(context)
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

    def run_test_mode(self, records: list[dict[str, str]]) -> None:
        target_outbox = self.args.test if "@" in self.args.test else "test@example.com"
        AppLogger.info(f"Test mode — sending to {target_outbox}")

        context = {
            "last_name": "TestUser",
            "title": "",
            "greeting": "TestUser",
            "paper_title": "Test Paper Title",
            "your_name": self.identity["your_name"],
            "your_paper_title": self.identity["your_paper_title"],
            "arxiv_category": self.identity["arxiv_category"],
        }
        rendered = self.templates.render_all(context)
        pdf_path = "my_paper.pdf"
        attach_pdf = bool(self.limits.get("attach_pdf", False)) and os.path.exists(pdf_path)

        acct_idx = 0
        if self.typed_accounts:
            acct_idx = 0

        context["recipient"] = target_outbox
        self.plugins.run_before_send(context)

        success, err_class, latency_ms, err_msg, _phases = self.smtp_engine.send_atomic(
            account_idx=acct_idx,
            recipient_email=target_outbox,
            subject=f"[TEST] {rendered['subject']}",
            text_content=rendered['text_body'],
            html_content=rendered['html_body'],
            attachment_path=pdf_path if attach_pdf else None,
        )

        result = {"success": success, "error": err_class.value, "latency_ms": latency_ms}
        self.plugins.run_after_send(context, result)

        if success:
            AppLogger.success("Test email sent.")
            self.stats["sent"] += 1
        else:
            AppLogger.error(f"Test failed: {err_class.value}: {err_msg}")

    def live_send(self, records: list[dict[str, str]], idx: int, engine: TemplateEngine | None = None, gkey: str | None = None) -> bool:
        engine = engine or self.templates
        target = records[idx]
        email = target["email"]
        email_hash = generate_sha256(email)

        def _advance(new_idx: int) -> None:
            if gkey:
                self.db.set_meta(f"current_index_{gkey}", str(new_idx))
            else:
                self.db.set_progress_index(new_idx)

        if not validate_email_format(email):
            AppLogger.warn(f"Invalid email at {idx}: '{email}'")
            self.stats["skipped"] += 1
            _advance(idx + 1)
            return True

        if self.db.is_email_sent(email_hash):
            AppLogger.warn(f"Duplicate at {idx}: {email}")
            self.stats["duplicates"] += 1
            _advance(idx + 1)
            return True

        recipient_id = self.db.upsert_recipient(target["last_name"], email, target["paper_title"], email_hash)

        context = {
            "last_name": target["last_name"],
            "title": target.get("title", ""),
            "greeting": self._build_greeting(target),
            "paper_title": target["paper_title"],
            "your_name": self.identity["your_name"],
            "your_paper_title": self.identity["your_paper_title"],
            "arxiv_category": self.identity["arxiv_category"],
            "recipient": email,
            "recipient_id": recipient_id,
        }

        self.plugins.run_before_send(context)
        rendered = engine.render_all(context)
        pdf_path = "my_paper.pdf"
        attach_pdf = bool(self.limits.get("attach_pdf", False)) and os.path.exists(pdf_path)

        # Render validation
        missing_vars = engine.validate_context(context)
        if missing_vars:
            AppLogger.warn(f"Template missing variables: {missing_vars}")

        qc = run_email_quality_checks(
            rendered["subject"], rendered["text_body"], rendered["html_body"],
            pdf_path if attach_pdf else None,
        )
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
            attachment_path=pdf_path if attach_pdf else None,
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
            doc.add(lambda: ("SMTP " + acct.email, True, acct.server + ":" + str(acct.port)))

        doc.add(doc.check_import("json"))
        doc.add(doc.check_import("csv"))
        doc.add(lambda: ("SQLite DB", True, "Connected"))

        if os.path.exists("my_paper.pdf"):
            doc.add(doc.check_file_exists("my_paper.pdf", "PDF Attachment"))

        doc.add(lambda: ("File Lock", not self.lock.is_locked(), "Not locked" if not self.lock.is_locked() else "Locked by " + str(FileLock.get_locked_pid())))

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

    def generate_preview_html(self, groups: list[dict[str, Any]]) -> None:
        html_parts = []
        card_id = 0

        def _card(tnum: int, rec: dict[str, str], engine: TemplateEngine, state: str, status: str) -> None:
            nonlocal card_id
            ctx = {
                "last_name": rec["last_name"],
                "title": rec.get("title", ""),
                "greeting": self._build_greeting(rec),
                "paper_title": rec["paper_title"],
                "your_name": self.identity["your_name"],
                "your_paper_title": self.identity["your_paper_title"],
                "arxiv_category": self.identity["arxiv_category"],
            }
            rendered = engine.render_all(ctx)
            initial = (rec['last_name'][0].upper() if rec['last_name'] else '?')
            html_parts.append(f"""\
        <article class="email-card {state}" data-state="{state}" data-template="{tnum}" data-name="{rec['last_name'].lower()}" data-email="{rec['email'].lower()}">
          <header class="card-head">
            <span class="status-badge {state}">{status}</span>
            <span class="tpl-badge" title="Template {tnum}">T{tnum}</span>
            <h3>{rec['last_name']}</h3>
            <span class="avatar">{initial}.</span>
            <button class="copy-btn" data-idx="{card_id}" title="Copy email body">⧉ Copy</button>
          </header>
          <p class="meta"><span class="lbl">To</span> <a class="mailto" href="mailto:{rec['email']}">{rec['email']}</a> <span class="lbl">Paper</span> {rec['paper_title']}</p>
          <p class="meta"><span class="lbl">Subject</span> {rendered['subject']}</p>
          <div class="body-preview" id="body-{card_id}">{rendered['text_body']}</div>
        </article>""")
            card_id += 1

        total = 0
        total_done = 0
        for g in groups:
            g_idx = g["idx"]
            total += len(g["records"])
            total_done += min(g_idx, len(g["records"]))
            for i, rec in enumerate(g["records"]):
                status = "✓ NEXT" if i == g_idx else ("✓ DONE" if i < g_idx else "—")
                state = "next" if i == g_idx else ("done" if i < g_idx else "pending")
                _card(g["tnum"], rec, g["engine"], state, status)

        multi = len(groups) > 1
        template_btns = ""
        template_counts = ""
        tpl_labels = []
        if multi:
            btns = ['<button class="fbtn tbtn active" data-tfilter="all" onclick="setTFilter(this)">All Templates</button>']
            for g in groups:
                btns.append(f'<button class="fbtn tbtn" data-tfilter="{g["tnum"]}" onclick="setTFilter(this)">Template {g["tnum"]} ({len(g["records"])})</button>')
                template_counts += f'<span>Template {g["tnum"]} <strong>{len(g["records"])}</strong></span>'
                tpl_labels.append(f'{g["tnum"]} {g["label"]}')
            template_btns = "".join(btns)

        tpl_line = " · ".join(tpl_labels) if tpl_labels else "1 default"
        next_idx = groups[0]["idx"] + 1 if groups else 1
        preview_html = f"""\
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>arXiv Dispatch — Preview</title>
<style>
  :root {{
    --bg:#171310; --bg2:#211b16; --card:#1d1813; --card-border:#3a3127;
    --text:#e8ddca; --text-dim:#b3a48c; --text-faint:#8a7a62;
    --rule:#3a3127; --ink:#e8ddca;
    --gold:#d9a441; --green:#7fb069; --red:#c96f4a; --blue:#7d9bb3;
    --paper:#f5efe2;
    --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,"Times New Roman",serif;
    --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  }}
  html.light {{
    --bg:#e9e2d2; --bg2:#f2ecdd; --card:#faf5e9; --card-border:#d6ccb4;
    --text:#2c261c; --text-dim:#5c513d; --text-faint:#8b7d63;
    --rule:#d6ccb4; --ink:#2c261c;
    --gold:#9a6a1f; --green:#4d7a35; --red:#a04f2d; --blue:#3d647c;
    --paper:#fffdf6;
  }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font:15px/1.7 var(--serif); background:var(--bg); color:var(--text);
         padding:36px clamp(16px,5vw,72px); transition:background .3s,color .3s; }}
  .masthead {{ display:flex; align-items:flex-end; justify-content:space-between;
               gap:16px; flex-wrap:wrap; padding-bottom:16px; margin-bottom:22px;
               border-bottom:2px solid var(--rule); }}
  .masthead h1 {{ font-size:clamp(19px,2.6vw,24px); font-weight:500; letter-spacing:.02em;
                  text-transform:uppercase; font-family:var(--mono); }}
  .masthead h1 .mark {{ color:var(--gold); }}
  .theme-toggle {{ cursor:pointer; border:1px solid var(--card-border); background:transparent;
                  color:var(--text-dim); border-radius:2px; padding:6px 14px;
                  font:500 11px var(--mono); letter-spacing:.08em; text-transform:uppercase;
                  transition:color .2s,border-color .2s; }}
  .theme-toggle:hover {{ color:var(--gold); border-color:var(--gold); }}
  .sub {{ color:var(--text-faint); font-size:12px; font-family:var(--mono);
          letter-spacing:.04em; margin-bottom:22px; }}
  .summary {{ display:flex; background:var(--card); border:1px solid var(--card-border);
              padding:0; margin-bottom:26px; flex-wrap:wrap; }}
  .summary span {{ padding:12px 22px; border-left:1px solid var(--card-border);
                   font-size:10.5px; color:var(--text-faint); font-family:var(--mono);
                   letter-spacing:.08em; text-transform:uppercase; }}
  .summary span:first-child {{ border-left:none; }}
  .summary strong {{ display:block; font:600 18px var(--serif); color:var(--ink);
                     letter-spacing:0; text-transform:none; margin-top:2px; }}
  .summary span.next strong {{ color:var(--gold); }}
  .email-card {{ background:var(--card); border:1px solid var(--card-border);
                 padding:24px 28px; margin-bottom:18px; position:relative; }}
  .email-card::before {{ content:""; position:absolute; top:-1px; left:0; right:0; height:2px; }}
  .email-card.next::before {{ background:linear-gradient(90deg,var(--gold),transparent 70%); }}
  .email-card.done::before {{ background:var(--card-border); }}
  .email-card.done {{ opacity:.6; }}
  .card-head {{ display:flex; align-items:baseline; gap:12px; margin-bottom:12px; }}
  .avatar {{ font:600 13px var(--mono); color:var(--gold); letter-spacing:.04em; }}
  .card-title {{ min-width:0; }}
  h3 {{ font-size:18px; font-weight:600; letter-spacing:.01em; }}
  .status-badge {{ display:inline-block; margin-right:8px; font:600 9.5px var(--mono);
                   letter-spacing:.14em; text-transform:uppercase; }}
  .status-badge.pending {{ color:var(--text-faint); }}
  .status-badge.next {{ color:var(--gold); }}
  .status-badge.done {{ color:var(--green); }}
  .tpl-badge {{ display:inline-block; margin-left:6px; font:600 9.5px var(--mono);
                letter-spacing:.1em; text-transform:uppercase; color:var(--blue); }}
  .meta {{ color:var(--text-dim); font-size:12.5px; font-family:var(--mono);
           margin-bottom:6px; overflow-wrap:anywhere; }}
  .meta .lbl {{ color:var(--text-faint); font-size:9.5px; letter-spacing:.1em; margin-right:8px; }}
  .mailto {{ color:var(--blue); text-decoration:none; }}
  .mailto:hover {{ text-decoration:underline; }}
  .body-preview {{ margin-top:14px; padding:18px 20px; background:var(--bg2);
                   border-left:3px solid var(--card-border); white-space:pre-wrap;
                   font:13.5px/1.8 var(--serif); color:var(--text-dim);
                   max-height:360px; overflow-y:auto; }}
  .body-preview::-webkit-scrollbar {{ width:8px; }}
  .body-preview::-webkit-scrollbar-thumb {{ background:var(--card-border); }}
  .foot {{ margin-top:28px; padding-top:14px; border-top:1px solid var(--rule);
           color:var(--text-faint); font:10.5px var(--mono); letter-spacing:.06em;
           display:flex; justify-content:space-between; flex-wrap:wrap; gap:8px; }}
  .toolbar {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:20px; }}
  .fbtn {{ cursor:pointer; border:1px solid var(--card-border); background:var(--card);
           color:var(--text-dim); border-radius:2px; padding:6px 14px;
           font:500 10.5px var(--mono); letter-spacing:.08em; text-transform:uppercase;
           transition:color .2s,border-color .2s; }}
  .fbtn:hover {{ color:var(--gold); border-color:var(--gold); }}
  .fbtn.active {{ color:var(--gold); border-color:var(--gold); }}
  .search {{ flex:1; min-width:200px; background:var(--card); border:1px solid var(--card-border);
             color:var(--text); border-radius:2px; padding:7px 12px;
             font:500 12px var(--mono); outline:none; }}
  .search::placeholder {{ color:var(--text-faint); }}
  .search:focus {{ border-color:var(--gold); }}
  .copy-btn {{ margin-left:auto; cursor:pointer; border:1px solid var(--card-border);
               background:transparent; color:var(--text-faint); border-radius:2px;
               padding:3px 10px; font:500 9.5px var(--mono); letter-spacing:.08em;
               text-transform:uppercase; transition:color .2s,border-color .2s; }}
  .copy-btn:hover {{ color:var(--gold); border-color:var(--gold); }}
  .copy-btn.copied {{ color:var(--green); border-color:var(--green); }}
  .empty {{ display:none; padding:30px; text-align:center; color:var(--text-faint);
            font:12px var(--mono); letter-spacing:.06em; border:1px dashed var(--card-border); }}
</style>
</head>
<body>
<header class="masthead">
  <h1><span class="mark">✦</span> arXiv Dispatch <span class="mark">/</span> Preview</h1>
  <button class="theme-toggle" id="themeBtn" onclick="toggleTheme()">🌙 Dark</button>
</header>
<p class="sub">OUTBOX — {total} endorsers loaded · {total_done} dispatched · templates: {tpl_line}</p>
<div class="toolbar">
  <button class="fbtn active" data-filter="all" onclick="setFilter(this)">All</button>
  <button class="fbtn" data-filter="pending" onclick="setFilter(this)">Pending</button>
  <button class="fbtn" data-filter="next" onclick="setFilter(this)">Next</button>
  <button class="fbtn" data-filter="done" onclick="setFilter(this)">Done</button>
  {template_btns}
  <input class="search" id="search" type="search" placeholder="Search name, email, paper…" oninput="applyFilter()">
</div>
<div class="summary">
  <span>Total <strong>{total}</strong></span>
  <span>Pending <strong>{total - total_done}</strong></span>
  <span>Done <strong>{total_done}</strong></span>
  <span class="next">Next <strong>#{next_idx}</strong></span>
  {template_counts}
</div>
{''.join(html_parts)}
<p class="empty" id="empty">No matches.</p>
<footer class="foot">
  <span>Generated {self._now_stamp()}</span>
  <span>arxiv-automation v5.2</span>
</footer>
<script>
  function toggleTheme() {{
    var html = document.documentElement;
    var dark = html.classList.toggle('dark');
    html.classList.toggle('light', !dark);
    document.getElementById('themeBtn').textContent = dark ? '🌙 Dark' : '☀️ Light';
    try {{ localStorage.setItem('theme', dark ? 'dark' : 'light'); }} catch(e) {{}}
  }}
  try {{
    var saved = localStorage.getItem('theme');
    if (saved === 'light') {{ toggleTheme(); }}
  }} catch(e) {{}}
  var currentFilter = 'all';
  var currentTFilter = 'all';
  function setTFilter(btn) {{
    currentTFilter = btn.dataset.tfilter;
    document.querySelectorAll('.tbtn').forEach(function(b) {{ b.classList.toggle('active', b === btn); }});
    applyFilter();
  }}
  function applyFilter() {{
    var q = document.getElementById('search').value.toLowerCase();
    var cards = document.querySelectorAll('.email-card');
    var visible = 0;
    cards.forEach(function(card) {{
      var show = currentFilter === 'all' || card.dataset.state === currentFilter;
      if (currentTFilter !== 'all') {{
        show = show && card.dataset.template === currentTFilter;
      }}
      if (show && q) {{
        show = (card.dataset.name + ' ' + card.dataset.email + ' ' + card.textContent.toLowerCase()).indexOf(q) !== -1;
      }}
      card.style.display = show ? '' : 'none';
      if (show) visible++;
    }});
    document.getElementById('empty').style.display = visible ? 'none' : 'block';
  }}
  function copyBody(idx, btn) {{
    var el = document.getElementById('body-' + idx);
    var text = el.innerText || el.textContent;
    function done() {{
      btn.classList.add('copied');
      btn.textContent = '✓ Copied';
      setTimeout(function() {{
        btn.classList.remove('copied');
        btn.textContent = '⧉ Copy';
      }}, 1500);
    }}
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(text).then(done, function() {{ fallbackCopy(text, done); }});
    }} else {{ fallbackCopy(text, done); }}
  }}
  function fallbackCopy(text, done) {{
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try {{ document.execCommand('copy'); }} catch(e) {{}}
    document.body.removeChild(ta);
    done();
  }}
  document.querySelectorAll('.copy-btn').forEach(function(b) {{
    b.addEventListener('click', function() {{ copyBody(b.dataset.idx, b); }});
  }});
</script>
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

    def _now_stamp(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M")

    def archive_sent_email(self, record: dict[str, str]) -> None:
        from datetime import datetime

        context = {
            "last_name": record["last_name"],
            "title": record.get("title", ""),
            "greeting": self._build_greeting(record),
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
