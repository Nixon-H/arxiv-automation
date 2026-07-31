import csv
import hashlib
import json
import os
import re
from dataclasses import dataclass

from core.exceptions import DataParserError
from core.logger import AppLogger
from core.validator import normalize_unicode, validate_email_format


@dataclass
class DuplicateStats:
    duplicate_emails: int = 0
    duplicate_name_paper: int = 0
    duplicate_exact: int = 0
    total_parsed: int = 0
    total_unique: int = 0

    @property
    def total_duplicates(self) -> int:
        return self.duplicate_emails + self.duplicate_name_paper + self.duplicate_exact

    def report(self) -> str:
        parts = []
        if self.duplicate_emails:
            parts.append(f"Email: {self.duplicate_emails}")
        if self.duplicate_name_paper:
            parts.append(f"Name/Paper: {self.duplicate_name_paper}")
        if self.duplicate_exact:
            parts.append(f"Exact: {self.duplicate_exact}")
        if not parts:
            return "No duplicates found"
        return f"Duplicates skipped: {' | '.join(parts)}"


def _record_hash(rec: dict[str, str]) -> str:
    return hashlib.sha256(
        f"{rec['email']}|{rec['last_name']}|{rec['paper_title']}".encode()
    ).hexdigest()


NAME_REGEX = re.compile(
    r"^([^,\n]+?)(?:\s+and\s+[^,\n]+?)?\s+(?:is|are)\s+qualified",
    re.IGNORECASE
)


TITLE_REGEX = re.compile(
    r"^(Dr\.?|Prof\.?|Professor|Mr\.?|Ms\.?|Miss|Mrs\.?|Sir|Madam)\b",
    re.IGNORECASE
)


def extract_title(full_line: str) -> str:
    match = TITLE_REGEX.match(full_line.strip())
    if match:
        title = match.group(1)
        return "Dr." if title.lower() == "dr" else "Mr." if title.lower() == "mr" else "Ms." if title.lower() == "ms" else title
    return ""


def clean_name(text: str) -> str:
    return re.sub(r"[\d\W_]+", " ", text).strip()


def extract_last_name(full_line: str) -> str:
    match = NAME_REGEX.match(full_line)
    if match:
        full_name = match.group(1).strip()
        clean = clean_name(full_name)
        parts = clean.split()
        if parts:
            return parts[-1]
    return "Professor"


class DataParser:
    @classmethod
    def auto_detect(cls, file_path: str) -> list[dict[str, str]]:
        records, _ = cls.auto_detect_with_stats(file_path)
        return records

    @classmethod
    def _sniff_format(cls, file_path: str) -> str:
        """Magic-byte sniffing: detect format from content when extension is
        missing, unknown, or the extension-based parser yielded nothing."""
        try:
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                head = f.read(2048).lstrip()
        except OSError:
            return "txt"

        if head.startswith(("{", "[")):
            return "json"
        if head.startswith(("---", "%YAML", "name:")) or "\nname:" in head[:512]:
            return "yaml"
        first_line = head.split("\n", 1)[0].lower()
        if any(k in first_line for k in ("last_name", "email", "paper_title")) and "," in first_line:
            return "csv"
        if first_line.count(",") >= 2:
            return "csv"
        return "txt"

    @classmethod
    def auto_detect_with_stats(cls, file_path: str) -> tuple[list[dict[str, str]], DuplicateStats]:
        if not os.path.exists(file_path):
            return [], DuplicateStats()

        ext = os.path.splitext(file_path)[1].lower()

        parsers = {
            ".txt": cls.parse_txt,
            ".csv": cls.parse_csv,
            ".json": cls.parse_json,
            ".yaml": cls.parse_yaml,
            ".yml": cls.parse_yaml,
            ".xlsx": cls.parse_xlsx,
        }

        raw = []
        if ext in parsers:
            raw = parsers[ext](file_path)
        if not raw:
            sniffed = cls._sniff_format(file_path)
            if sniffed != ext.lstrip("."):
                AppLogger.info(f"Extension {ext or '(none)'} yielded nothing — sniffed content as {sniffed}")
            sniff_parser = parsers.get(f".{sniffed}", cls.parse_txt)
            raw = sniff_parser(file_path)
        if not raw:
            AppLogger.warn(f"No records parsed from {file_path}")
            return [], DuplicateStats()

        clean, stats = cls._deduplicate(raw)
        AppLogger.info(f"Parsed {stats.total_parsed} records, unique: {stats.total_unique}")
        if stats.total_duplicates:
            AppLogger.warn(stats.report())
        return clean, stats

    @classmethod
    def _deduplicate(cls, records: list[dict[str, str]]) -> tuple[list[dict[str, str]], DuplicateStats]:
        seen_emails: set = set()
        seen_name_paper: set = set()
        seen_hashes: set = set()
        clean: list[dict[str, str]] = []
        stats = DuplicateStats(total_parsed=len(records))

        for rec in records:
            email = rec["email"].lower().strip()
            name_key = (rec["last_name"].lower().strip(), rec["paper_title"].lower().strip())
            h = _record_hash(rec)

            if h in seen_hashes:
                stats.duplicate_exact += 1
                continue
            if email in seen_emails:
                stats.duplicate_emails += 1
                continue
            if name_key in seen_name_paper:
                stats.duplicate_name_paper += 1
                continue

            seen_emails.add(email)
            seen_name_paper.add(name_key)
            seen_hashes.add(h)
            clean.append(rec)

        stats.total_unique = len(clean)
        return clean, stats

    @classmethod
    def parse_txt(cls, file_path: str) -> list[dict[str, str]]:
        if not os.path.exists(file_path):
            return []

        with open(file_path, encoding="utf-8", errors="ignore") as f:
            content = f.read()

        raw_blocks = [b.strip() for b in content.split("\n\n") if b.strip()]
        records: list[dict[str, str]] = []

        for block_idx, block in enumerate(raw_blocks):
            lines = [line.strip() for line in block.split("\n") if line.strip()]
            if len(lines) < 2:
                AppLogger.warn(
                    f"Block #{block_idx} too short ({len(lines)} lines), skipping"
                )
                continue

            if "|" in lines[0]:
                records.extend(cls._parse_pipe_block(block_idx, lines))
                continue

            destination_email = lines[-1]
            if not validate_email_format(destination_email):
                AppLogger.warn(
                    f"Block #{block_idx}: last line not a valid email: '{destination_email}'"
                )
                continue

            first_line = lines[0]
            paper_title = lines[1]
            last_name = extract_last_name(first_line)
            title = extract_title(first_line)

            records.append({
                "last_name": normalize_unicode(last_name),
                "title": title,
                "email": destination_email.lower().strip(),
                "paper_title": normalize_unicode(paper_title),
            })

        return records

    @classmethod
    def _parse_pipe_block(cls, block_idx: int, lines: list[str]) -> list[dict[str, str]]:
        """Handle `Name | email | paper title` lines (pipe-separated TXT)."""
        out: list[dict[str, str]] = []
        for line in lines:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                AppLogger.warn(f"Pipe line #{block_idx}: expected 3 fields (name | email | paper), got {len(parts)}")
                continue
            name, email, paper = parts[0], parts[1], " | ".join(parts[2:])
            if not validate_email_format(email):
                AppLogger.warn(f"Pipe line #{block_idx}: invalid email: '{email}'")
                continue
            title = extract_title(name)
            last_name = extract_last_name(name)
            if title:
                rest = re.sub(r"^" + re.escape(title), "", name, flags=re.IGNORECASE).strip()
            else:
                rest = name
            cleaned = clean_name(rest)
            name_parts = cleaned.split()
            if name_parts:
                last_name = name_parts[-1]
            out.append({
                "last_name": normalize_unicode(last_name),
                "title": title,
                "email": email.lower().strip(),
                "paper_title": normalize_unicode(paper),
            })
        return out

    @classmethod
    def parse_csv(cls, file_path: str) -> list[dict[str, str]]:
        if not os.path.exists(file_path):
            return []

        records: list[dict[str, str]] = []
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if "email" in row and "last_name" in row and "paper_title" in row:
                    email = row["email"].strip().lower()
                    if not validate_email_format(email):
                        AppLogger.warn(f"Invalid email in CSV: '{email}'")
                        continue
                    records.append({
                        "last_name": normalize_unicode(row["last_name"].strip()),
                        "email": email,
                        "paper_title": normalize_unicode(row["paper_title"].strip()),
                    })
        return records

    @classmethod
    def parse_json(cls, file_path: str) -> list[dict[str, str]]:
        if not os.path.exists(file_path):
            return []

        with open(file_path, encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise DataParserError(f"JSON parse error: {e}")

        records: list[dict[str, str]] = []
        if isinstance(data, list):
            for item in data:
                if "email" in item and "last_name" in item and "paper_title" in item:
                    email = str(item["email"]).strip().lower()
                    if not validate_email_format(email):
                        continue
                    records.append({
                        "last_name": normalize_unicode(str(item["last_name"])),
                        "email": email,
                        "paper_title": normalize_unicode(str(item["paper_title"])),
                    })
        return records

    @classmethod
    def parse_yaml(cls, file_path: str) -> list[dict[str, str]]:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            AppLogger.warn("PyYAML not installed. Skipping YAML parsing.")
            return []

        if not os.path.exists(file_path):
            return []

        with open(file_path, encoding="utf-8") as f:
            try:
                data = yaml.safe_load(f)
            except Exception as e:
                raise DataParserError(f"YAML parse error: {e}")

        records: list[dict[str, str]] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and all(k in item for k in ("email", "last_name", "paper_title")):
                    email = str(item["email"]).strip().lower()
                    if not validate_email_format(email):
                        continue
                    records.append({
                        "last_name": normalize_unicode(str(item["last_name"])),
                        "email": email,
                        "paper_title": normalize_unicode(str(item["paper_title"])),
                    })
        return records

    @classmethod
    def parse_xlsx(cls, file_path: str) -> list[dict[str, str]]:
        try:
            import openpyxl
        except ImportError:
            AppLogger.warn("openpyxl not installed. Skipping XLSX parsing.")
            return []

        if not os.path.exists(file_path):
            return []

        try:
            wb = openpyxl.load_workbook(file_path, read_only=True)
            ws = wb.active
            if ws is None:
                return []

            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                return []

            header = [str(c).lower() if c else "" for c in rows[0]]
            records: list[dict[str, str]] = []

            for row in rows[1:]:
                row_data = {header[i]: str(row[i]).strip() if row[i] else "" for i in range(len(row))}
                if row_data.get("email") and row_data.get("last_name") and row_data.get("paper_title"):
                    email = row_data["email"].lower()
                    if not validate_email_format(email):
                        continue
                    records.append({
                        "last_name": normalize_unicode(row_data["last_name"]),
                        "email": email,
                        "paper_title": normalize_unicode(row_data["paper_title"]),
                    })

            wb.close()
            return records
        except Exception as e:
            raise DataParserError(f"XLSX parse error: {e}")
