import csv
import json
import os
from datetime import datetime
from typing import Any

from core.logger import AppLogger
from core.tracker import ProgressTracker


class ReportExporter:
    def __init__(self, output_dir: str = "exports") -> None:
        self.output_dir = output_dir

    def ensure_dir(self) -> None:
        os.makedirs(self.output_dir, exist_ok=True)

    def export_json(self, stats: dict[str, Any], tracker: ProgressTracker | None = None) -> str:
        self.ensure_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.output_dir, f"run_report_{timestamp}.json")

        report = {
            "timestamp": datetime.now().isoformat(),
            "stats": {
                "sent": stats.get("sent", 0),
                "failed": stats.get("failed", 0),
                "skipped": stats.get("skipped", 0),
                "duplicates": stats.get("duplicates", 0),
                "retries": stats.get("retries", 0),
                "total_processed": stats.get("total_processed", 0),
            },
        }

        if tracker:
            report["tracker"] = {
                "current_index": tracker.current_index,
                "last_sent_timestamp": tracker.last_sent_timestamp,
                "sent_history_count": len(tracker.sent_history_hashes),
                "last_error": tracker.last_error,
            }
            health_data = {}
            for email, health in tracker.account_health.items():
                health_data[email] = health.to_dict()
            report["account_health"] = health_data

        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=4)

        AppLogger.success(f"JSON report exported: {path}")
        return path

    def export_csv(self, stats: dict[str, Any], tracker: ProgressTracker | None = None) -> str:
        self.ensure_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.output_dir, f"run_report_{timestamp}.csv")

        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Metric", "Value"])
            writer.writerow(["Timestamp", datetime.now().isoformat()])
            writer.writerow(["Sent", stats.get("sent", 0)])
            writer.writerow(["Failed", stats.get("failed", 0)])
            writer.writerow(["Skipped", stats.get("skipped", 0)])
            writer.writerow(["Duplicates", stats.get("duplicates", 0)])
            writer.writerow(["Retries", stats.get("retries", 0)])

            if tracker:
                writer.writerow([])
                writer.writerow(["Current Index", tracker.current_index])
                writer.writerow(["Sent History Count", len(tracker.sent_history_hashes)])
                writer.writerow(["Last Error", tracker.last_error])

                for email, health in tracker.account_health.items():
                    writer.writerow([])
                    writer.writerow([f"Account: {email}", ""])
                    for key, val in health.to_dict().items():
                        if key != "email":
                            writer.writerow([f"  {key}", val])

        AppLogger.success(f"CSV report exported: {path}")
        return path

    def _build_svg_pie(self, values: dict[str, int], size: int = 200) -> str:
        total = sum(values.values()) or 1
        colors = ["#3498db", "#e74c3c", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c", "#e67e22", "#34495e"]
        cx, cy, r = size // 2, size // 2, size // 3
        start: float = 0
        slices = []
        legend = []
        for i, (label, val) in enumerate(values.items()):
            pct = val / total
            angle = 360 * pct
            end = start + angle
            rad_start = 3.14159 * start / 180
            rad_end = 3.14159 * end / 180
            x1 = cx + r * __import__('math').cos(rad_start)
            y1 = cy + r * __import__('math').sin(rad_start)
            x2 = cx + r * __import__('math').cos(rad_end)
            y2 = cy + r * __import__('math').sin(rad_end)
            large = 1 if angle > 180 else 0
            color = colors[i % len(colors)]
            slices.append(
                f'<path d="M{cx},{cy} L{x1:.1f},{y1:.1f} A{r},{r} 0 {large},1 {x2:.1f},{y2:.1f} Z" '
                f'fill="{color}" stroke="white" stroke-width="1"/>'
            )
            legend.append(
                f'<tr><td style="width:12px;height:12px;background:{color};border-radius:2px"></td>'
                f'<td style="padding-left:6px;font-size:12px">{label}</td>'
                f'<td style="padding-left:10px;font-size:12px;font-weight:bold">{val}</td></tr>'
            )
            start = end
        legend_html = f'<table>{"".join(legend)}</table>'
        return f'<div style="display:flex;align-items:center;gap:20px"><svg width="{size}" height="{size}">{"".join(slices)}</svg>{legend_html}</div>'

    def _build_svg_bar(self, labels: list, values: list, title: str, bar_color: str = "#3498db", height: int = 200) -> str:
        if not values:
            return f"<p>No {title.lower()} data</p>"
        max_v = max(values) or 1
        bar_w = max(20, min(60, 400 // len(values)))
        total_w = max(400, len(values) * (bar_w + 10))
        bars = []
        for i, (lbl, val) in enumerate(zip(labels, values)):
            h = max(2, int((val / max_v) * (height - 30)))
            x = 40 + i * (bar_w + 10)
            y = height - h - 20
            bars.append(
                f'<rect x="{x}" y="{y}" width="{bar_w}" height="{h}" fill="{bar_color}" rx="2">'
                f'<title>{lbl}: {val}</title></rect>'
                f'<text x="{x + bar_w//2}" y="{height - 5}" font-size="9" text-anchor="middle" '
                f'transform="rotate(-45,{x + bar_w//2},{height - 5})">{lbl[:8]}</text>'
            )
        return f'<h4>{title}</h4><svg width="{total_w}" height="{height}"><rect width="100%" height="100%" fill="#fafafa" rx="4"/>{"".join(bars)}</svg>'

    def _fetch_send_timeline(self, db=None) -> str:
        if not db:
            return "<p>No database connection</p>"
        try:
            sends = db.fetchall(
                "SELECT timestamp, status, latency_ms FROM sends ORDER BY id DESC LIMIT 50"
            )
            if not sends:
                return "<p>No send history</p>"
            labels = [s["timestamp"][5:16] if s["timestamp"] else str(i) for i, s in enumerate(sends)]
            labels.reverse()
            vals = [s["latency_ms"] or 0 for s in sends]
            cols = ["#27ae60" if s["status"] == "success" else "#e74c3c" for s in sends]
            cols.reverse()
            return self._build_svg_bar(labels, vals, "Latency Timeline (ms)", bar_color="#2ecc71")
        except Exception:
            return "<p>Timeline unavailable</p>"

    def export_html(self, stats: dict[str, Any], tracker: ProgressTracker | None = None, db=None) -> str:
        self.ensure_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.output_dir, f"dashboard_{timestamp}.html")

        sent = stats.get("sent", 0)
        failed = stats.get("failed", 0)
        skipped = stats.get("skipped", 0)
        duplicates = stats.get("duplicates", 0)
        duplicates_in_file = stats.get("duplicates_in_file", 0)
        retries = stats.get("retries", 0)
        total = sent + failed + skipped + duplicates
        success_rate = (sent / total * 100) if total > 0 else 0

        pie_data = {"Sent": sent, "Failed": failed, "Skipped": skipped}
        pie_svg = self._build_svg_pie(pie_data)

        dup_data = {"File Dupes": duplicates_in_file, "DB Dupes": duplicates, "Unique": max(0, total - duplicates - duplicates_in_file)}
        dup_pie = self._build_svg_pie(dup_data)

        timeline_svg = self._fetch_send_timeline(db)

        health_rows = ""
        if tracker:
            for email, health in tracker.account_health.items():
                h = health.to_dict()
                status_color = "#27ae60" if not health.is_suspended else "#e74c3c"
                total = h['total_sent']
                failed = h['failures_today']
                sp = (total * 100.0 / (total + failed)) if (total + failed) else 0
                health_rows += f"""
                <tr>
                    <td>{email}</td>
                    <td style="color:{status_color}">{'Suspended' if health.is_suspended else 'Active'}</td>
                    <td>{h['sent_today']}</td>
                    <td>{h['failures_today']}</td>
                    <td>{h['auth_failures']}</td>
                    <td>{h['total_sent']}</td>
                    <td>{sp:.0f}%</td>
                </tr>"""

        avg_latency = stats.get("avg_latency", 0)

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Dashboard - {timestamp}</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f0f2f5; color: #333; padding: 30px; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        h1 {{ color: #2c3e50; font-size: 1.6em; margin-bottom: 5px; }}
        .subtitle {{ color: #888; font-size: 0.9em; margin-bottom: 25px; }}
        .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 25px; }}
        .card {{ background: white; border-radius: 10px; padding: 18px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
        .card .val {{ font-size: 1.8em; font-weight: 700; }}
        .card .lbl {{ font-size: 0.8em; color: #888; margin-top: 4px; }}
        .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }}
        .panel {{ background: white; border-radius: 10px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); overflow-x: auto; }}
        .panel h3 {{ font-size: 1em; color: #555; margin-bottom: 12px; border-bottom: 2px solid #eee; padding-bottom: 8px; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.9em; }}
        th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee; }}
        th {{ background: #f8f9fa; font-weight: 600; color: #555; }}
        tr:hover {{ background: #f5f7fa; }}
        .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.8em; font-weight: 600; }}
        .badge-green {{ background: #d4edda; color: #155724; }}
        .badge-red {{ background: #f8d7da; color: #721c24; }}
        @media (max-width: 768px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}
    </style>
</head>
<body>
<div class="container">
    <h1>Dispatch Dashboard</h1>
    <p class="subtitle">Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — Arxiv Dispatch v5.0</p>

    <div class="cards">
        <div class="card"><div class="val" style="color:#27ae60">{sent}</div><div class="lbl">Sent</div></div>
        <div class="card"><div class="val" style="color:#e74c3c">{failed}</div><div class="lbl">Failed</div></div>
        <div class="card"><div class="val" style="color:#f39c12">{skipped}</div><div class="lbl">Skipped</div></div>
        <div class="card"><div class="val" style="color:#9b59b6">{duplicates + duplicates_in_file}</div><div class="lbl">Dupes</div></div>
        <div class="card"><div class="val" style="color:#2c3e50">{success_rate:.1f}%</div><div class="lbl">Success Rate</div></div>
        <div class="card"><div class="val" style="color:#2980b9">{avg_latency}ms</div><div class="lbl">Avg Latency</div></div>
        <div class="card"><div class="val" style="color:#1abc9c">{retries}</div><div class="lbl">Retries</div></div>
    </div>

    <div class="grid-2">
        <div class="panel">
            <h3>Outcome Distribution</h3>
            {pie_svg}
        </div>
        <div class="panel">
            <h3>Duplicate Breakdown</h3>
            {dup_pie}
        </div>
    </div>

    <div class="panel" style="margin-bottom:20px">
        <h3>Send Timeline (latency ms, last 50)</h3>
        {timeline_svg}
    </div>

    <div class="panel">
        <h3>Account Health</h3>
        <table>
            <tr>
                <th>Email</th><th>Status</th><th>Today</th><th>Failures</th><th>Auth Fail</th><th>Total</th><th>SR</th>
            </tr>
            {health_rows or '<tr><td colspan="7">No account data</td></tr>'}
        </table>
    </div>
</div>
</body>
</html>"""

        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

        AppLogger.success(f"Dashboard exported: {path}")
        return path

    def export_all(self, stats: dict[str, Any], tracker: ProgressTracker | None = None, db=None) -> None:
        self.export_json(stats, tracker)
        self.export_csv(stats, tracker)
        self.export_html(stats, tracker, db=db)
        AppLogger.success("All reports exported to exports/")
