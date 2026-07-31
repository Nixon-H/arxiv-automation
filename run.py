#!/usr/bin/env python3
"""
Production-Grade Automated Endorsement Dispatch System v5.0
Author: Nixon-H
"""

import argparse
import sys

from core.exceptions import AutomationError, FileLockError
from core.logger import AppLogger
from core.wizard import run_wizard
from engine.orchestrator import OrchestrationRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Enterprise arXiv Endorsement Dispatch Engine v5.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python run.py --dry-run                    # Preview next record
  python run.py --test user@example.com      # Send test email
  python run.py --live                       # Send one live email
  python run.py --live --send 5              # Send batch of 5
  python run.py --stats                      # Display execution stats
  python run.py --verify                     # Pre-flight validation
  python run.py --doctor                     # Full system diagnostic
  python run.py --metrics 9090               # Prometheus metrics endpoint
  python run.py --scheduler                  # Generate cron/systemd config
  python run.py --export-report              # Generate JSON/CSV/HTML reports
  python run.py --validate-config            # Validate configuration
  python run.py --reset-progress             # Reset all tracking data
        """,
    )

    group = parser.add_mutually_exclusive_group(required=True)

    group.add_argument("--test", nargs="?", const="test@example.com", metavar="EMAIL",
                       help="Send a test email to verify configuration")
    group.add_argument("--live", action="store_true",
                       help="Execute live production dispatch")
    group.add_argument("--dry-run", action="store_true",
                       help="Preview next dispatch without sending")
    group.add_argument("--reset-progress", action="store_true",
                       help="Reset all progress tracking in SQLite")
    group.add_argument("--stats", action="store_true",
                       help="Display execution statistics")
    group.add_argument("--verify", action="store_true",
                       help="Run pre-flight validation checks")
    group.add_argument("--doctor", nargs="?", const="", metavar="BUNDLE",
                       help="Full system diagnostic report (--doctor=bundle to create diagnostics.zip)")
    group.add_argument("--export-report", action="store_true",
                       help="Export reports (JSON/CSV/HTML)")
    group.add_argument("--validate-config", action="store_true",
                       help="Validate configuration")
    group.add_argument("--retry-failed", action="store_true",
                       help="Retry previously failed dispatches")
    group.add_argument("--scheduler", action="store_true",
                       help="Generate cron/systemd scheduler config")
    group.add_argument("--metrics", nargs="?", const="9090", metavar="PORT",
                       help="Start Prometheus metrics endpoint")
    group.add_argument("--init", action="store_true",
                       help="Interactive configuration wizard")
    group.add_argument("--followups", nargs="?", const="7", metavar="DAYS",
                       help="List recipients due for follow-up (default 7 days; combine with --send N to send)")
    group.add_argument("--mark-replied", metavar="EMAIL",
                       help="Mark a recipient as replied (stops follow-ups)")

    parser.add_argument("--send", type=int, dest="send_n", default=0,
                        help="Number of emails to send in batch (with --live)")
    parser.add_argument("--locale", type=str, default="en",
                        help="Locale for i18n (en, fr, zh, etc.)")
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not open browser for preview (used with --dry-run)")
    parser.add_argument("--template", type=str, default="",
                        help="Template set name (e.g. 'citation' → template_citation.txt/.html); default: template.txt/.html")
    parser.add_argument("--contacts", type=str, default="",
                        help="Endorser list file to use instead of auto-detected endorsers.txt (e.g. endorsers_transformer.txt)")
    parser.add_argument("--group", action="append", default=[], metavar="FILE:TEMPLATE",
                        help="Extra dispatch group: endorser file + template set name, e.g. --group endorsers_transformer.txt:citation; repeatable for 1, 2, 3+ templates")

    return parser


def main() -> None:
    AppLogger.initialize()

    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.init:
            run_wizard()
            return
        runner = OrchestrationRunner(args)
        runner.process_execution_pipeline()
    except FileLockError as e:
        AppLogger.error(f"Lock error: {e}")
        sys.exit(1)
    except AutomationError as e:
        AppLogger.error(f"Automation error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        AppLogger.warn("Interrupted by user.")
        sys.exit(130)
    except Exception as e:
        AppLogger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
