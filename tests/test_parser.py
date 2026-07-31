import os
import json
import tempfile
import pytest
from parsing.parser import DataParser, DataParserError, DuplicateStats, _record_hash


SAMPLE_TXT_BLOCKS = """\
Dr. Alice Smith
Quantum Computing
alice@mit.edu

Prof. Bob Jones
ML Theory
bob@stanford.edu

Dr. Carol Lee
NLP
carol@berkeley.edu
"""


class TestDataParser:
    def test_parse_txt_basic(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        f.write(SAMPLE_TXT_BLOCKS)
        f.close()
        try:
            records = DataParser.auto_detect(f.name)
            assert len(records) == 3
            assert records[0]["email"] == "alice@mit.edu"
        finally:
            os.unlink(f.name)

    def test_parse_txt_single_block(self):
        content = "Dr. Alice Smith\nQuantum Computing\nalice@mit.edu\n"
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        try:
            records = DataParser.auto_detect(f.name)
            assert len(records) == 1
            assert records[0]["email"] == "alice@mit.edu"
        finally:
            os.unlink(f.name)

    def test_parse_csv_basic(self, csv_file):
        records = DataParser.auto_detect(csv_file)
        assert len(records) == 2
        assert records[0]["email"] == "alice@mit.edu"

    def test_parse_json_basic(self, json_file):
        records = DataParser.auto_detect(json_file)
        assert len(records) == 2
        assert records[0]["paper_title"] == "Quantum Computing"

    def test_parse_yaml_basic(self, yaml_file):
        records = DataParser.auto_detect(yaml_file)
        assert len(records) == 2
        assert records[1]["last_name"] == "Jones"

    def test_parse_auto_detect_txt(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        f.write(SAMPLE_TXT_BLOCKS)
        f.close()
        try:
            records = DataParser.auto_detect(f.name)
            assert len(records) == 3
        finally:
            os.unlink(f.name)

    def test_parse_auto_detect_csv(self, csv_file):
        records = DataParser.auto_detect(csv_file)
        assert len(records) == 2

    def test_parse_unknown_extension(self):
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False, mode="w") as f:
            f.write("test\n")
            p = f.name
        try:
            records = DataParser.auto_detect(p)
            assert records == []
        finally:
            os.unlink(p)

    def test_parse_file_not_found(self):
        records = DataParser.auto_detect("/nonexistent/file.txt")
        assert records == []

    def test_parse_empty_file(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            p = f.name
        try:
            records = DataParser.auto_detect(p)
            assert records == []
        finally:
            os.unlink(p)

    def test_dedup_with_stats_integration(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        f.write(SAMPLE_TXT_BLOCKS)
        f.close()
        try:
            records, stats = DataParser.auto_detect_with_stats(f.name)
            assert isinstance(stats, DuplicateStats)
            assert len(records) == 3
        finally:
            os.unlink(f.name)

    def test_parse_max_cols(self):
        content = "last_name,email,paper_title,extra\nCol1,col2@x.com,TitleX,extra_col\n"
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        try:
            records = DataParser.auto_detect(f.name)
            assert len(records) == 1
        finally:
            os.unlink(f.name)


class TestDuplicateStats:
    def test_dedup_email(self):
        records = [
            {"last_name": "Smith", "email": "a@m.com", "paper_title": "Q"},
            {"last_name": "Jones", "email": "a@m.com", "paper_title": "R"},
            {"last_name": "Adams", "email": "b@m.com", "paper_title": "S"},
        ]
        deduped, stats = DataParser._deduplicate(records)
        assert len(deduped) == 2
        assert stats.duplicate_emails == 1

    def test_dedup_name_paper(self):
        records = [
            {"last_name": "Smith", "email": "a@m.com", "paper_title": "Q"},
            {"last_name": "Smith", "email": "b@m.com", "paper_title": "Q"},
            {"last_name": "Jones", "email": "c@m.com", "paper_title": "R"},
        ]
        deduped, stats = DataParser._deduplicate(records)
        assert len(deduped) == 2
        assert stats.duplicate_name_paper == 1

    def test_dedup_exact(self):
        records = [
            {"last_name": "Smith", "email": "a@m.com", "paper_title": "Q"},
            {"last_name": "Smith", "email": "a@m.com", "paper_title": "Q"},
        ]
        deduped, stats = DataParser._deduplicate(records)
        assert len(deduped) == 1
        assert stats.duplicate_exact == 1

    def test_dedup_all_unique(self):
        records = [
            {"last_name": "A", "email": "a@m.com", "paper_title": "Q"},
            {"last_name": "B", "email": "b@m.com", "paper_title": "R"},
        ]
        deduped, stats = DataParser._deduplicate(records)
        assert len(deduped) == 2
        assert stats.total_duplicates == 0

    def test_dedup_normalization(self):
        records = [
            {"last_name": "Smith", "email": "A@M.COM", "paper_title": "Q"},
            {"last_name": "Smith", "email": "a@m.com", "paper_title": "Q"},
        ]
        deduped, stats = DataParser._deduplicate(records)
        assert len(deduped) == 1

    def test_stats_report(self):
        stats = DuplicateStats(duplicate_emails=3, duplicate_name_paper=2, duplicate_exact=1)
        report = stats.report()
        assert "Email" in report
        assert "Name/Paper" in report
        assert "Exact" in report

    def test_stats_empty_report(self):
        stats = DuplicateStats()
        assert stats.report() == "No duplicates found"

    def test_stats_total_calc(self):
        stats = DuplicateStats(duplicate_emails=3, duplicate_name_paper=2, duplicate_exact=1)
        assert stats.total_duplicates == 6

    def test_dedup_with_duplicate_input(self):
        dup_csv = "last_name,email,paper_title\nSmith,a@m.com,Q\nSmith,a@m.com,Q\n"
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8")
        f.write(dup_csv)
        f.close()
        try:
            records, stats = DataParser.auto_detect_with_stats(f.name)
            assert len(records) == 1
            assert stats.duplicate_exact == 1
            assert stats.total_duplicates == 1
        finally:
            os.unlink(f.name)

    def test_record_hash_consistency(self):
        r1 = {"last_name": "Smith", "email": "a@m.com", "paper_title": "Q"}
        r2 = {"last_name": "Smith", "email": "a@m.com", "paper_title": "Q"}
        assert _record_hash(r1) == _record_hash(r2)

    def test_record_hash_different(self):
        r1 = {"last_name": "Smith", "email": "a@m.com", "paper_title": "Q"}
        r2 = {"last_name": "Jones", "email": "b@m.com", "paper_title": "R"}
        assert _record_hash(r1) != _record_hash(r2)
