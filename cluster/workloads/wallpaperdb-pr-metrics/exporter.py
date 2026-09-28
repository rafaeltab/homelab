"""Expose GitHub PR merge dates as categorical Prometheus gauges."""
import json
import logging
import os
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from urllib.request import Request, urlopen

REPO = "rafaeltab/wallpaperdb"
URL = f"https://api.github.com/repos/{REPO}/pulls?state=closed&per_page=100"
REFRESH_SECONDS = 1800
counts = Counter()
last_success = 0.0
last_error = 0.0
lock = threading.Lock()


def fetch_counts(open_url=urlopen):
    result = Counter()
    page_url = URL
    for _ in range(100):
        request = Request(page_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "wallpaperdb-pr-metrics/1"})
        with open_url(request, timeout=20) as response:
            payload = json.load(response)
            links = response.headers.get("Link", "")
        if not isinstance(payload, list):
            raise ValueError("GitHub did not return a PR list")
        for pr in payload:
            merged_at = pr.get("merged_at")
            if merged_at:
                result[merged_at[:10]] += 1  # GitHub dates are UTC.
        page_url = None
        for link in links.split(","):
            if 'rel="next"' in link:
                page_url = link.strip().split(";", 1)[0].strip("<>")
        if not page_url:
            return result
        if urlparse(page_url).netloc != "api.github.com":
            raise ValueError("Unexpected GitHub pagination host")
    raise ValueError("GitHub pagination exceeded 100 pages")


def refresh():
    global counts, last_success, last_error
    try:
        updated = fetch_counts()
        with lock:
            counts = updated
            last_success = time.time()
            last_error = 0.0
        logging.info("refreshed %d merge dates, %d merges", len(updated), sum(updated.values()))
    except Exception:
        with lock:
            last_error = time.time()
        logging.exception("GitHub refresh failed; keeping previous snapshot")


def metrics():
    with lock:
        snapshot, success, error = counts.copy(), last_success, last_error
    lines = [
        "# HELP wallpaperdb_pr_merges_daily Number of PRs merged on this UTC date.",
        "# TYPE wallpaperdb_pr_merges_daily gauge",
    ]
    lines.extend(f'wallpaperdb_pr_merges_daily{{day="{day}"}} {snapshot[day]}' for day in sorted(snapshot))
    lines += [
        "# HELP wallpaperdb_pr_metrics_last_success_seconds Unix time of last successful GitHub refresh.",
        "# TYPE wallpaperdb_pr_metrics_last_success_seconds gauge",
        f"wallpaperdb_pr_metrics_last_success_seconds {success}",
        "# HELP wallpaperdb_pr_metrics_last_error_seconds Unix time of last failed GitHub refresh (zero if healthy).",
        "# TYPE wallpaperdb_pr_metrics_last_error_seconds gauge",
        f"wallpaperdb_pr_metrics_last_error_seconds {error}",
    ]
    return ("\n".join(lines) + "\n").encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            body = metrics()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        elif self.path == "/healthz":
            with lock:
                healthy = bool(last_success) and (time.time() - last_success < REFRESH_SECONDS * 3)
            body = b"ok\n" if healthy else b"stale\n"
            self.send_response(200 if healthy else 503)
            self.send_header("Content-Type", "text/plain")
        else:
            self.send_error(404)
            return
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    logging.basicConfig(level=logging.INFO)
    def poll():
        while True:
            refresh()
            time.sleep(REFRESH_SECONDS)
    threading.Thread(target=poll, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "8080"))), Handler).serve_forever()


if __name__ == "__main__":
    main()
