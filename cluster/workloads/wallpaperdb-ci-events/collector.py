"""Metadata-only public GitHub Actions -> completion-time Loki events.
No third-party packages, credentials, logs, artifacts, steps or source reads.
"""
import collections
import datetime as dt
import email.utils
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = "rafaeltab/wallpaperdb"
SERVICE = "wallpaperdb-ci-events"
DAY = 86400
CATEGORIES = {"Build, Lint, and Test": "build-lint-test", "E2E Tests": "e2e", "Container E2E Tests": "container-e2e"}
CONCLUSIONS = {"success", "failure", "cancelled", "skipped", "timed_out", "action_required", "neutral", "stale", "startup_failure"}


def stamp(value):
    if not isinstance(value, str):
        raise ValueError("missing timestamp")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp without timezone")
    return int(parsed.timestamp())


def iso(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def event(run, attempt, job):
    """Allowlist only. Never pass through whole source API objects."""
    if job.get("status") != "completed":
        return None
    try:
        end = stamp(job.get("completed_at"))
    except (ValueError, TypeError, OverflowError):
        return None  # No authoritative event time: explicit omission counter at caller.
    conclusion = job.get("conclusion")
    conclusion = conclusion if conclusion in CONCLUSIONS else "other"
    data = {"schema_version": 1, "event_type": "job", "repository": REPO,
            "run_id": run["id"], "attempt": attempt, "job_id": job["id"],
            "workflow": run.get("name") or "unknown", "workflow_path": run.get("path") or "unknown",
            "job": job.get("name") or "unknown", "branch": run.get("head_branch") or "unknown",
            "event": run.get("event") or "unknown", "conclusion": conclusion,
            "runner_labels": job.get("labels") or [],
            "source_url": f"https://github.com/{REPO}/actions/runs/{run['id']}/job/{job['id']}",
            "started_at": job.get("started_at"), "completed_at": job["completed_at"],
            "completed_seconds": end, "duration_valid": False}
    try:
        duration = end - stamp(job.get("started_at"))
        if conclusion == "skipped":
            data["duration_reason"] = "skipped"
        elif duration < 0:
            data["duration_reason"] = "negative_duration"
        else:
            data.update(duration_seconds=duration, duration_valid=True, duration_reason="valid")
    except (ValueError, TypeError, OverflowError):
        data["duration_reason"] = "missing_or_invalid_start"
    return data


class Deferred(Exception):
    """Rate budget or upstream backoff; not permission to add credentials."""


class APIError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(f"GitHub HTTP {status}")


class Collector:
    def __init__(self, path, clock=time.time, transport=None, loki=None):
        self.clock = clock
        self.transport = transport or self.http
        self.loki = loki or os.environ.get("LOKI_URL", "http://loki-gateway.observability.svc.cluster.local/loki/api/v1/push")
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS calls (at REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS tasks (run INTEGER, attempt INTEGER, payload TEXT NOT NULL,
            page INTEGER NOT NULL DEFAULT 1, ready REAL NOT NULL DEFAULT 0,
            initial_pending INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(run,attempt));
          CREATE TABLE IF NOT EXISTS outbox (identity TEXT PRIMARY KEY, ts INTEGER NOT NULL,
            labels TEXT NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending');
        ''')
        if not self.get("bootstrap", ""):
            with self.db:
                self.set("bootstrap", int(clock()))
                self.set("floor", int(clock()) - 6 * DAY)
                self.set("scan_floor", int(clock()) - 6 * DAY)
                self.set("scan_page", 1)
                self.set("scan_due", 0)

    @staticmethod
    def http(url, body=None):
        headers = {"User-Agent": "wallpaperdb-ci-events/1", "Accept": "application/vnd.github+json"}
        if body is not None:
            headers = {"Content-Type": "application/json", "User-Agent": "wallpaperdb-ci-events/1"}
        # No Authorization, environment-token, gh CLI, redirect to authenticated API, or credential fallback.
        req = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, dict(response.headers), response.read()

    def get(self, key, default="0"):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(value)))

    def count(self, key, amount=1):
        self.set(key, int(self.get(key)) + amount)

    def github(self, path):
        now = self.clock()
        with self.db:
            self.db.execute("DELETE FROM calls WHERE at<=?", (now - 3600,))
            used = self.db.execute("SELECT count(*) FROM calls").fetchone()[0]
            if now < float(self.get("blocked_until")) or used >= 30:
                raise Deferred("public GitHub rate budget")
            # Debit BEFORE network IO. Crashes and failed requests consume budget too.
            self.db.execute("INSERT INTO calls VALUES (?)", (now,))
        try:
            status, headers, raw = self.transport("https://api.github.com/repos/" + REPO + path)
        except urllib.error.HTTPError as exc:
            status, headers, raw = exc.code, dict(exc.headers), b""
        headers = {k.lower(): v for k, v in headers.items()}
        with self.db:
            self.set("last_api_status", status)
            if status in (403, 429) or headers.get("x-ratelimit-remaining") == "0":
                retry = headers.get("retry-after", "600")
                try:
                    until = now + float(retry)
                except ValueError:
                    until = email.utils.parsedate_to_datetime(retry).timestamp()
                self.set("blocked_until", max(until, float(headers.get("x-ratelimit-reset", "0"))))
        if status in (403, 429):
            raise Deferred("upstream GitHub rate limit")
        if status != 200:
            raise APIError(status)
        return json.loads(raw)

    def discover(self):
        now = self.clock()
        if now < float(self.get("scan_due")):
            return False
        page = int(self.get("scan_page", "1"))
        floor = int(self.get("scan_floor"))
        query = urllib.parse.urlencode({"created": ">=" + iso(floor), "per_page": 100, "page": page})
        result = self.github("/actions/runs?" + query)
        runs = result["workflow_runs"]
        with self.db:
            for run in runs:
                # Retain only explicitly allowed run fields. API steps/content never reach SQLite.
                data = {k: run.get(k) for k in ("id", "name", "path", "head_branch", "event", "status", "updated_at")}
                for attempt in range(1, int(run.get("run_attempt", 1)) + 1):
                    key = f"done:{run['id']}:{attempt}"
                    if self.get(key, ""):
                        continue
                    self.db.execute("INSERT INTO tasks(run,attempt,payload) VALUES (?,?,?) "
                                    "ON CONFLICT(run,attempt) DO UPDATE SET payload=excluded.payload",
                                    (run["id"], attempt, json.dumps(data, sort_keys=True)))
            if len(runs) == 100:
                self.set("scan_page", page + 1)
            else:
                self.set("scan_page", 1)
                self.set("scan_due", now + 600)
                self.set("last_discovery", int(now))
                self.set("scan_complete", 1)
                # Fixed six-day creation horizon, not a moving page boundary mid-pagination.
                self.set("scan_floor", int(now) - 6 * DAY)
        return True

    def discover_reruns(self):
        """Daily unfiltered traversal finds activity on older-created runs.

        A persisted fixed cutoff tolerates a multi-hour, rate-limited traversal.
        No creation filter: GitHub's filtered-search 1000-result ceiling does
        not apply. Pagination is not a snapshot; daily overlap repairs shifts.
        """
        now = self.clock()
        if now < float(self.get("rerun_due")):
            return False
        page = int(self.get("rerun_page", "1"))
        floor = int(self.get("rerun_floor", str(int(now) - 6 * DAY)))
        result = self.github(f"/actions/runs?per_page=100&page={page}")
        runs = result["workflow_runs"]
        with self.db:
            for run in runs:
                # updated_at changes on reruns, unlike created_at. Missing
                # update metadata is investigated rather than silently ignored.
                try:
                    if stamp(run.get("updated_at")) < floor:
                        continue
                except (ValueError, TypeError, OverflowError):
                    pass
                data = {k: run.get(k) for k in ("id", "name", "path", "head_branch", "event", "status", "updated_at")}
                for attempt in range(1, int(run.get("run_attempt", 1)) + 1):
                    if self.get(f"done:{run['id']}:{attempt}", ""):
                        continue
                    self.db.execute("INSERT INTO tasks(run,attempt,payload) VALUES (?,?,?) "
                                    "ON CONFLICT(run,attempt) DO UPDATE SET payload=excluded.payload",
                                    (run["id"], attempt, json.dumps(data, sort_keys=True)))
            self.set("rerun_floor", floor)
            if len(runs) == 100:
                self.set("rerun_page", page + 1)
            else:
                self.set("rerun_page", 1)
                self.set("rerun_due", now + DAY)
                self.set("rerun_floor", int(now) - 6 * DAY)
                self.set("last_rerun_scan", int(now))
        return True

    def fetch_task(self):
        task = self.db.execute("SELECT * FROM tasks WHERE ready<=? ORDER BY ready,run,attempt LIMIT 1", (self.clock(),)).fetchone()
        if not task:
            return False
        run = json.loads(task["payload"])
        try:
            if run.get("status") != "completed":
                attempt_data = self.github(f"/actions/runs/{task['run']}/attempts/{task['attempt']}")
                run["status"] = attempt_data["status"]
                with self.db:
                    self.db.execute("UPDATE tasks SET payload=? WHERE run=? AND attempt=?",
                                    (json.dumps(run, sort_keys=True), task["run"], task["attempt"]))
            result = self.github(f"/actions/runs/{task['run']}/attempts/{task['attempt']}/jobs?per_page=100&page={task['page']}")
        except Deferred:
            raise
        except Exception as exc:
            with self.db:
                if isinstance(exc, APIError) and exc.status in (404, 410):
                    self.count("unavailable_attempts")
                    self.set(f"done:{task['run']}:{task['attempt']}", 1)
                    self.db.execute("DELETE FROM tasks WHERE run=? AND attempt=?", (task["run"], task["attempt"]))
                else:
                    self.count("api_failures")
                    self.db.execute("UPDATE tasks SET ready=? WHERE run=? AND attempt=?",
                                    (self.clock()+600, task["run"], task["attempt"]))
            return True  # A single failure must not starve other eligible tasks.
        jobs = result["jobs"]
        with self.db:
            for job in jobs:
                data = event(run, task["attempt"], job)
                if data:
                    self.enqueue(data)
                elif job.get("status") == "completed":
                    identity = f"invalid:{run['id']}:{task['attempt']}:{job['id']}"
                    if not self.get(identity, ""):
                        self.count("missing_completion")
                        self.set(identity, 1)
            incomplete = any(j.get("status") != "completed" for j in jobs)
            if incomplete:
                self.set(f"incomplete:{task['run']}:{task['attempt']}", 1)
            if len(jobs) == 100:
                self.db.execute("UPDATE tasks SET page=page+1 WHERE run=? AND attempt=?", (task["run"], task["attempt"]))
            else:
                self.db.execute("UPDATE tasks SET initial_pending=0 WHERE run=? AND attempt=?", (task["run"], task["attempt"]))
                incomplete = incomplete or self.get(f"incomplete:{task['run']}:{task['attempt']}") == "1"
                if incomplete or run.get("status") != "completed":
                    self.db.execute("UPDATE tasks SET page=1,ready=? WHERE run=? AND attempt=?", (self.clock()+600, task["run"], task["attempt"]))
                    self.set(f"incomplete:{task['run']}:{task['attempt']}", 0)
                else:
                    self.set(f"done:{task['run']}:{task['attempt']}", 1)
                    self.db.execute("DELETE FROM tasks WHERE run=? AND attempt=?", (task["run"], task["attempt"]))
        return True

    def enqueue(self, data):
        identity = f"{REPO}:{data['run_id']}:{data['attempt']}:{data['job_id']}"
        ts = data["completed_seconds"]
        labels = {"service_name": SERVICE, "repository": REPO, "event_type": "job",
                  "job_category": CATEGORIES.get(data["job"], "other"), "conclusion": data["conclusion"],
                  "phase": "initial" if ts <= int(self.get("bootstrap")) else "live",
                  "completion_slot": str((ts // 1800) % 336)}
        state = "outside_backfill" if ts < int(self.get("floor")) else "pending"
        inserted = self.db.execute("INSERT OR IGNORE INTO outbox(identity,ts,labels,body,state) VALUES (?,?,?,?,?)",
                        (identity, ts, json.dumps(labels, sort_keys=True), json.dumps(data, sort_keys=True, separators=(",", ":")), state)).rowcount
        if inserted and state == "outside_backfill":
            self.count("outside_backfill")

    def initial_ready(self):
        # Barrier is first full traversal, not completion of all live/in-progress runs.
        if self.get("initial_sealed") == "1":
            return True
        ready = self.get("scan_complete") == "1" and not self.db.execute(
            "SELECT 1 FROM tasks WHERE initial_pending=1 LIMIT 1").fetchone()
        if ready:
            with self.db:
                self.set("initial_sealed", 1)
        return ready

    def flush(self):
        now = int(self.clock())
        # Fixed 336-value half-hour ring partitions by EVENT time, not ingestion.
        # Within each admitted generation timestamps differ by <30m, safely
        # inside the verified 1h Loki reorder window. Generations are 168h
        # apart; the 167h admission guard prevents overlap. Late tasks and
        # failed initial fetches cannot advance another slot's highwater.
        # There is deliberately no global initial-history delivery barrier.
        rows = self.db.execute("SELECT * FROM outbox WHERE state='pending' ORDER BY ts,identity LIMIT 1000").fetchall()
        grouped = collections.defaultdict(list)
        for row in rows:
            labels = json.loads(row["labels"])
            reason = "expired" if row["ts"] < now - 167 * 3600 else ""
            if row["ts"] > now:
                reason = "future_completion"
            if reason:
                with self.db:
                    self.db.execute("UPDATE outbox SET state=? WHERE identity=?", (reason, row["identity"]))
                    self.count(reason)
                continue
            grouped[row["labels"]].append(row)
        for labels, values in grouped.items():
            # Small deterministic chunks; crash retries use identical labels/time/body.
            for start in range(0, len(values), 100):
                batch = values[start:start+100]
                payload = {"streams": [{"stream": json.loads(labels), "values": [[str(v["ts"] * 10**9), v["body"]] for v in batch]}]}
                status, _, _ = self.transport(self.loki, json.dumps(payload).encode())
                if status != 204:
                    raise RuntimeError(f"Loki HTTP {status}")
                with self.db:
                    self.db.executemany("UPDATE outbox SET state='sent' WHERE identity=?", [(r["identity"],) for r in batch])
                    self.set("highwater:" + labels, max(int(self.get("highwater:" + labels)), batch[-1]["ts"]))
                    self.set("last_delivery", now)

    def health(self, error=""):
        pending = self.db.execute("SELECT count(*),min(ts) FROM outbox WHERE state='pending'").fetchone()
        sent = self.db.execute("SELECT count(*),min(ts),max(ts) FROM outbox WHERE state='sent'").fetchone()
        tasks = self.db.execute("SELECT count(*) FROM tasks").fetchone()[0]
        return {"schema_version": 1, "event_type": "collector_status", "pending_events": pending[0],
                "pending_attempts": tasks, "oldest_pending_seconds": pending[1], "sent_events": sent[0],
                "oldest_event_seconds": sent[1], "newest_event_seconds": sent[2],
                "last_discovery_seconds": int(self.get("last_discovery")), "last_delivery_seconds": int(self.get("last_delivery")),
                "last_rerun_scan_seconds": int(self.get("last_rerun_scan")),
                "rerun_scan_page": int(self.get("rerun_page", "1")),
                "github_requests_last_hour": self.db.execute("SELECT count(*) FROM calls WHERE at>?", (self.clock()-3600,)).fetchone()[0],
                "initial_ready": self.initial_ready(), "error": error,
                **{k: int(self.get(k)) for k in ("expired", "outside_backfill", "out_of_order", "future_completion", "missing_completion", "last_api_status", "unavailable_attempts", "api_failures")}}

    def cycle(self):
        error = ""
        try:
            self.discover()
            self.discover_reruns()
            # Share a hard 30/hour budget with discovery; retain task page on every failure.
            for _ in range(4):
                if not self.fetch_task():
                    break
        except Deferred:
            error = "github_budget_deferred"
        except Exception as exc:
            error = type(exc).__name__  # No API payloads, URLs containing credentials or exception text.
        try:
            # GitHub failure must never prevent healthy Loki outbox delivery.
            self.flush()
        except Exception as exc:
            error = "delivery_" + type(exc).__name__
        data = self.health(error)
        body = json.dumps(data, sort_keys=True, separators=(",", ":"))
        print(body, flush=True)  # Local diagnostics survive a failed status export.
        payload = {"streams": [{"stream": {"service_name": SERVICE, "repository": REPO, "event_type": "collector_status"},
                                "values": [[str(int(self.clock()) * 10**9), body]]}]}
        status, _, _ = self.transport(self.loki, json.dumps(payload).encode())
        if status != 204:
            raise RuntimeError(f"Loki status HTTP {status}")
        return data


def main():
    collector = Collector(os.environ.get("STATE_PATH", "/state/collector.sqlite"))
    while True:
        try:
            collector.cycle()
        except Exception as exc:
            print(json.dumps({"event_type": "collector_operational_failure", "error": type(exc).__name__}), flush=True)
        time.sleep(600)


if __name__ == "__main__":
    main()
