import importlib
import os
import subprocess
import sys
from collections.abc import Callable

from core.logger import AppLogger

CheckFunc = Callable[[], tuple[str, bool, str]]


class Doctor:
    def __init__(self) -> None:
        self.checks: list[CheckFunc] = []
        self.passed = 0
        self.failed = 0
        self.warnings = 0

    def add(self, check: CheckFunc) -> None:
        if isinstance(check, tuple):
            # Allow direct (name, ok, msg) tuples as shorthand
            self.checks.append(lambda t=check: t)
        else:
            self.checks.append(check)

    def run_all(self) -> bool:
        AppLogger.info("Running full diagnostic (--doctor) ...")
        print()

        for check in self.checks:
            try:
                name, ok, msg = check()
            except Exception as e:
                name = "unknown"
                ok = False
                msg = str(e)

            status = "PASS" if ok else ("WARN" if "warn" in name.lower() else "FAIL")
            if ok:
                self.passed += 1
            elif "WARN" in status:
                self.warnings += 1
            else:
                self.failed += 1

            print(f"  [{status:4}] {name}: {msg}")

        print()
        total = self.passed + self.failed + self.warnings
        AppLogger.info(f"Results: {self.passed} passed, {self.failed} failed, {self.warnings} warnings / {total} total")

        return self.failed == 0

    @staticmethod
    def check_file_exists(path: str, label: str = "") -> CheckFunc:
        def _check() -> tuple[str, bool, str]:
            ok = os.path.exists(path)
            size = os.path.getsize(path) if ok else 0
            msg = f"Found ({size} bytes)" if ok else "Missing"
            return label or path, ok, msg
        return _check

    @staticmethod
    def check_import(module: str) -> CheckFunc:
        def _check() -> tuple[str, bool, str]:
            try:
                importlib.import_module(module)
                return f"import {module}", True, "OK"
            except ImportError:
                return f"import {module}", False, "Not installed"
        return _check

    @staticmethod
    def check_writable(path: str) -> CheckFunc:
        def _check() -> tuple[str, bool, str]:
            try:
                os.makedirs(path, exist_ok=True)
                test_file = os.path.join(path, ".write_test")
                with open(test_file, "w") as f:
                    f.write("ok")
                os.remove(test_file)
                return f"writable {path}", True, "OK"
            except Exception as e:
                return f"writable {path}", False, str(e)
        return _check

    @staticmethod
    def check_command(cmd: str, args: str = "--version") -> CheckFunc:
        def _check() -> tuple[str, bool, str]:
            try:
                result = subprocess.run(
                    [cmd, args], capture_output=True, text=True, timeout=5,
                )
                out = (result.stdout or result.stderr).strip()[:60]
                return f"command {cmd}", True, out
            except FileNotFoundError:
                return f"command {cmd}", False, "Not found in PATH"
            except Exception as e:
                return f"command {cmd}", False, str(e)
        return _check

    @staticmethod
    def check_python_version() -> CheckFunc:
        def _check() -> tuple[str, bool, str]:
            v = sys.version_info
            ok = v.major >= 3 and v.minor >= 10
            return "python version", ok, f"{v.major}.{v.minor}.{v.micro}"
        return _check

    @staticmethod
    def check_attachment(path: str) -> CheckFunc:
        def _check() -> tuple[str, bool, str]:
            if not os.path.exists(path):
                return f"attachment {path}", False, "Not found"
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                header = f.read(12)
            is_pdf = header[:5] == b"%PDF-"
            details = [f"{size} bytes"]
            if is_pdf:
                details.append("PDF")
            else:
                mime_guess = "application/pdf" if path.endswith(".pdf") else "unknown"
                details.append(mime_guess)
            if size > 25 * 1024 * 1024:
                return f"attachment {path}", False, f"{' | '.join(details)} — exceeds 25MB"
            return f"attachment {path}", True, " | ".join(details)
        return _check

    @staticmethod
    def check_template_diff(path: str) -> CheckFunc:
        def _check() -> tuple[str, bool, str]:
            if not os.path.exists(path):
                return f"diff {path}", True, "No template yet"
            try:
                result = subprocess.run(
                    ["git", "diff", "HEAD", "--", path],
                    capture_output=True, text=True, timeout=5,
                    cwd=os.path.dirname(os.path.abspath(path)) or ".",
                )
                diff = result.stdout.strip()
                if diff:
                    lines = diff.count("\n")
                    return f"diff {path}", True, f"{lines} lines changed from last commit"
                return f"diff {path}", True, "No changes since last commit"
            except Exception:
                return f"diff {path}", True, "Not a git repo or git unavailable"
        return _check

    @staticmethod
    def check_email_auth(domain: str) -> CheckFunc:
        from core.dns_validator import check_dkim, check_dmarc, check_spf
        def _check() -> tuple[str, bool, str]:
            spf_ok, spf_msg = check_spf(domain)
            dkim_ok, dkim_msg = check_dkim(domain)
            dmarc_ok, dmarc_msg = check_dmarc(domain)
            results = []
            results.append(f"SPF={'✓' if spf_ok else '✗'}")
            results.append(f"DKIM={'✓' if dkim_ok else '✗'}")
            results.append(f"DMARC={'✓' if dmarc_ok else '✗'}")
            all_ok = spf_ok
            return f"email auth [{domain}]", all_ok, " | ".join(results)
        return _check

    @staticmethod
    def create_bundle(output: str = "diagnostics.zip") -> str:
        import zipfile
        files: dict[str, str] = {}
        if os.path.exists("config.json"):
            files["config.json"] = "config.json"
        for d, label in [("logs", "logs"), ("data", "data"), ("templates", "templates"), ("plugins", "plugins")]:
            if os.path.isdir(d):
                for root, _, fnames in os.walk(d):
                    for fn in fnames:
                        rel = os.path.join(root, fn)
                        files[rel] = rel
        for fn in ["template.txt", "template.html", "endorsers.txt", "preview.html", ".env"]:
            if os.path.exists(fn):
                files[fn] = fn
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
            for arcname, fpath in files.items():
                try:
                    zf.write(fpath, arcname)
                except Exception:
                    pass
        AppLogger.success(f"Diagnostic bundle written to {os.path.abspath(output)}")
        return os.path.abspath(output)
