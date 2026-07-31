import os
from exports.report import ReportExporter


def test_export_json(tmp_path):
    exporter = ReportExporter(str(tmp_path))
    stats = {"sent": 5, "failed": 1, "skipped": 0, "duplicates": 0, "retries": 0, "total_processed": 6}
    path = exporter.export_json(stats)
    assert os.path.exists(path)
    with open(path) as f:
        import json
        data = json.load(f)
    assert data["stats"]["sent"] == 5


def test_export_csv(tmp_path):
    exporter = ReportExporter(str(tmp_path))
    stats = {"sent": 3, "failed": 0, "skipped": 0, "duplicates": 0, "retries": 0, "total_processed": 3}
    path = exporter.export_csv(stats)
    assert os.path.exists(path)


def test_export_html(tmp_path):
    exporter = ReportExporter(str(tmp_path))
    stats = {"sent": 10, "failed": 2, "skipped": 1, "duplicates": 0, "retries": 1, "total_processed": 13}
    path = exporter.export_html(stats)
    assert os.path.exists(path)
    with open(path) as f:
        html = f.read()
    assert "Dashboard" in html


def test_export_all(tmp_path):
    exporter = ReportExporter(str(tmp_path))
    stats = {"sent": 1, "failed": 0, "skipped": 0, "duplicates": 0, "retries": 0, "total_processed": 1}
    exporter.export_all(stats)
    files = os.listdir(str(tmp_path))
    assert len(files) >= 3


def test_export_duplicates(tmp_path):
    exporter = ReportExporter(str(tmp_path))
    stats = {"sent": 5, "failed": 0, "skipped": 0, "duplicates": 2, "duplicates_in_file": 3, "retries": 0, "total_processed": 5}
    path = exporter.export_html(stats)
    assert os.path.exists(path)