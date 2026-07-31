import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any

from core.database import Database


class MetricsRegistry:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list] = {}

    def counter_inc(self, name: str, value: int = 1) -> None:
        self._counters[name] = self._counters.get(name, 0) + value

    def gauge_set(self, name: str, value: float) -> None:
        self._gauges[name] = value

    def histogram_observe(self, name: str, value: float) -> None:
        if name not in self._histograms:
            self._histograms[name] = []
        self._histograms[name].append(value)

    def snapshot(self) -> dict[str, Any]:
        metrics: dict[str, Any] = {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": {},
        }

        for name, values in self._histograms.items():
            if values:
                metrics["histograms"][name] = {
                    "count": len(values),
                    "sum": round(sum(values), 3),
                    "avg": round(sum(values) / len(values), 3),
                    "min": round(min(values), 3),
                    "max": round(max(values), 3),
                }

        if self.db:
            stats = self.db.get_send_stats()
            metrics["counters"]["db_total_sends"] = stats.get("total", 0)
            metrics["counters"]["db_successful_sends"] = stats.get("sent", 0)
            metrics["counters"]["db_failed_sends"] = stats.get("failed", 0)
            metrics["gauges"]["db_progress_index"] = self.db.get_progress_index()

        metrics["timestamp"] = time.time()
        return metrics

    def prometheus_format(self) -> str:
        snap = self.snapshot()
        lines = ['# HELP arxiv_dispatch_metrics Runtime metrics for arXiv dispatch']
        lines.append('# TYPE arxiv_dispatch_metrics gauge')

        for name, val in snap.get("counters", {}).items():
            key = name.replace(" ", "_").replace("-", "_")
            lines.append(f'{key}_total {val}')

        for name, val in snap.get("gauges", {}).items():
            key = name.replace(" ", "_").replace("-", "_")
            lines.append(f'{key} {val}')

        for name, h in snap.get("histograms", {}).items():
            key = name.replace(" ", "_").replace("-", "_")
            lines.append(f'{key}_count {h["count"]}')
            lines.append(f'{key}_sum {h["sum"]}')
            lines.append(f'{key}_avg {h["avg"]}')

        return "\n".join(lines)


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/metrics":
            metrics = getattr(self.server, "metrics_registry", None)
            if metrics:
                data = metrics.prometheus_format()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data.encode())
            else:
                self.send_response(503)
                self.end_headers()
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            data = json.dumps({"status": "ok"}).encode()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass


def start_metrics_server(registry: MetricsRegistry, port: int = 9090) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", port), MetricsHandler)
    setattr(server, "metrics_registry", registry)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
