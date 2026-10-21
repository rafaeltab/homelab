import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error

spec = importlib.util.spec_from_file_location("collector", Path(__file__).with_name("collector.py"))
assert spec is not None
c = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
spec.loader.exec_module(c)
NOW = c.stamp("2026-10-21T12:00:00Z")
RUN = {"id": 10, "name": "CI", "path": ".github/workflows/ci.yml", "head_branch": "main", "event": "push", "status": "completed", "run_attempt": 1}
JOB = {"id": 20, "name": "Build, Lint, and Test", "status": "completed", "conclusion": "success", "started_at": "2026-10-21T09:41:15Z", "completed_at": "2026-10-21T09:52:46Z", "labels": ["ubuntu-latest"]}


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "state.sqlite")
        self.now = NOW
        self.requests = []
        self.responses: list = []
        self.fail_push = False
        self.collector = c.Collector(self.path, clock=lambda: self.now, transport=self.transport)

    def tearDown(self):
        self.collector.db.close()
        self.tmp.cleanup()

    def transport(self, url, body=None):
        self.requests.append((url, body))
        if body is not None:
            if self.fail_push:
                raise OSError("isolated failure")
            return 204, {}, b""
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def reply(self, data):
        self.responses.append((200, {}, json.dumps(data).encode()))

    def enqueue(self, job=None, run=None, attempt=1):
        with self.collector.db:
            self.collector.enqueue(c.event(run or RUN, attempt, job or JOB))

    def seal(self):
        with self.collector.db:
            self.collector.set("scan_complete", 1)

    def test_success_actual_duration(self):
        data = c.event(RUN, 1, JOB)
        self.assertEqual(data["duration_seconds"], 691)
        self.assertEqual(data["completed_seconds"], c.stamp(JOB["completed_at"]))

    def test_failure_not_success(self):
        data = c.event(RUN, 1, dict(JOB, conclusion="failure"))
        self.assertEqual(data["conclusion"], "failure")
        self.assertTrue(data["duration_valid"])

    def test_cancellation_is_separate(self):
        self.assertEqual(c.event(RUN, 1, dict(JOB, conclusion="cancelled"))["conclusion"], "cancelled")

    def test_skip_is_not_zero_duration(self):
        data = c.event(RUN, 1, dict(JOB, conclusion="skipped"))
        self.assertFalse(data["duration_valid"])
        self.assertNotIn("duration_seconds", data)

    def test_missing_start_is_unavailable(self):
        data = c.event(RUN, 1, dict(JOB, started_at=None))
        self.assertFalse(data["duration_valid"])
        self.assertEqual(data["duration_reason"], "missing_or_invalid_start")

    def test_negative_duration(self):
        data = c.event(RUN, 1, dict(JOB, started_at="2026-10-22T00:00:00Z"))
        self.assertFalse(data["duration_valid"])
        self.assertEqual(data["duration_reason"], "negative_duration")

    def test_missing_completion_never_retimestamped(self):
        self.assertIsNone(c.event(RUN, 1, dict(JOB, completed_at=None)))

    def test_naive_completion_never_accepted(self):
        self.assertIsNone(c.event(RUN, 1, dict(JOB, completed_at="2026-10-21T09:52:46")))

    def test_incomplete_not_emitted(self):
        self.assertIsNone(c.event(RUN, 1, dict(JOB, status="in_progress")))

    def test_metadata_allowlist(self):
        data = c.event(dict(RUN, head_commit={"message": "SECRET"}), 1, dict(JOB, steps=[{"name": "SECRET"}], log="SECRET"))
        self.assertNotIn("SECRET", json.dumps(data))
        self.assertNotIn("steps", data)

    def test_matrix_name_remains_json_only(self):
        self.enqueue(dict(JOB, name="build (node=22, os=linux)"))
        row = self.collector.db.execute("SELECT * FROM outbox").fetchone()
        labels = json.loads(row["labels"])
        self.assertEqual(labels["job_category"], "other")
        self.assertEqual(set(labels), {"service_name", "repository", "event_type", "job_category", "conclusion", "phase", "completion_slot"})
        self.assertEqual(json.loads(row["body"])["job"], "build (node=22, os=linux)")

    def test_duplicate_fetch_deduplicates(self):
        self.enqueue()
        self.enqueue()
        self.assertEqual(self.collector.db.execute("SELECT count(*) FROM outbox").fetchone()[0], 1)

    def test_rerun_attempts_are_distinct(self):
        self.enqueue(attempt=1)
        self.enqueue(attempt=2)
        self.assertEqual(self.collector.db.execute("SELECT count(*) FROM outbox").fetchone()[0], 2)

    def test_budget_survives_restart_and_failed_calls(self):
        self.responses = [OSError("offline")] * 30
        for _ in range(30):
            with self.assertRaises(OSError):
                self.collector.github("/actions/runs")
        self.collector.db.close()
        self.collector = c.Collector(self.path, clock=lambda: self.now, transport=self.transport)
        with self.assertRaises(c.Deferred):
            self.collector.github("/actions/runs")
        self.assertEqual(len(self.requests), 30)
        self.now += 3601
        self.reply({"workflow_runs": []})
        self.collector.github("/actions/runs")

    def test_retry_after_and_remaining_header(self):
        self.responses = [(429, {"Retry-After": "120", "X-RateLimit-Reset": str(NOW+500)}, b"")]
        with self.assertRaises(c.Deferred):
            self.collector.github("/actions/runs")
        self.now += 121
        with self.assertRaises(c.Deferred):
            self.collector.github("/actions/runs")
        self.assertEqual(len(self.requests), 1)

    def test_success_with_zero_remaining_sets_backoff(self):
        self.responses = [(200, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(NOW+1000)}, b"{}")]
        self.collector.github("/actions/runs")
        with self.assertRaises(c.Deferred):
            self.collector.github("/actions/runs")

    def test_pagination_cursor_does_not_move_on_failure(self):
        self.reply({"workflow_runs": [dict(RUN, id=i) for i in range(100)]})
        self.collector.discover()
        self.assertEqual(self.collector.get("scan_page"), "2")
        self.assertIn("created=", self.requests[0][0])
        self.responses = [OSError("partial page")]
        with self.assertRaises(OSError):
            self.collector.discover()
        self.assertEqual(self.collector.get("scan_page"), "2")
        self.reply({"workflow_runs": []})
        self.collector.discover()
        self.assertEqual(self.collector.get("scan_page"), "1")
        self.assertEqual(self.collector.get("scan_complete"), "1")

    def test_jobs_attempt_endpoint_and_page_transaction(self):
        self.reply({"workflow_runs": [dict(RUN, run_attempt=2)]})
        self.collector.discover()
        self.reply({"jobs": [dict(JOB, id=i) for i in range(100)]})
        self.collector.fetch_task()
        self.assertIn("/runs/10/attempts/1/jobs?per_page=100&page=1", self.requests[-1][0])
        self.assertEqual(self.collector.db.execute("SELECT page FROM tasks WHERE attempt=1").fetchone()[0], 2)
        self.responses = [OSError("failed page")]
        self.collector.fetch_task()
        self.assertEqual(self.collector.db.execute("SELECT page FROM tasks WHERE attempt=1").fetchone()[0], 2)
        self.now += 601
        with self.collector.db:
            self.collector.db.execute("UPDATE tasks SET ready=? WHERE attempt=2", (self.now+600,))
        self.reply({"jobs": []})
        self.collector.fetch_task()
        self.assertTrue(self.collector.get("done:10:1", ""))

    def test_incomplete_revisited(self):
        self.reply({"workflow_runs": [RUN]})
        self.collector.discover()
        self.reply({"jobs": [dict(JOB, status="in_progress")]})
        self.collector.fetch_task()
        self.assertFalse(self.collector.fetch_task())
        self.now += 601
        self.reply({"jobs": [JOB]})
        self.assertTrue(self.collector.fetch_task())
        self.assertTrue(self.collector.get("done:10:1", ""))

    def test_chronological_batch_without_global_barrier(self):
        self.enqueue()
        older = dict(JOB, id=21, completed_at="2026-10-21T09:51:46Z")
        self.enqueue(older)
        self.collector.flush()
        values = json.loads(self.requests[0][1])["streams"][0]["values"]
        self.assertEqual([json.loads(v[1])["job_id"] for v in values], [21, 20])
        self.assertEqual(values[1][0], str(c.stamp(JOB["completed_at"])*10**9))

    def test_crash_retry_is_identical(self):
        self.enqueue()
        self.seal()
        self.fail_push = True
        with self.assertRaises(OSError):
            self.collector.flush()
        original = self.requests[-1][1]
        self.collector.db.close()
        self.collector = c.Collector(self.path, clock=lambda: self.now, transport=self.transport)
        self.fail_push = False
        self.collector.flush()
        self.assertEqual(self.requests[-1][1], original)
        self.assertEqual(self.collector.health()["sent_events"], 1)
        self.collector.flush()
        self.assertEqual(len(self.requests), 2)

    def test_expired_not_retimestamped(self):
        self.enqueue()
        self.seal()
        self.now += 7*c.DAY
        self.collector.flush()
        self.assertEqual(self.requests, [])
        self.assertEqual(self.collector.health()["expired"], 1)

    def test_late_old_completion_delivered_in_event_time_slot(self):
        self.enqueue()
        self.seal()
        self.collector.flush()
        self.enqueue(dict(JOB, id=21, started_at="2026-10-20T00:00:00Z", completed_at="2026-10-20T00:01:00Z"))
        self.collector.flush()
        self.assertEqual(self.collector.health()["out_of_order"], 0)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.collector.health()["sent_events"], 2)

    def test_invalid_completion_omission_count_deduplicates(self):
        for _ in range(2):
            self.reply({"workflow_runs": [RUN]})
            with self.collector.db:
                self.collector.set("scan_due", 0)
                self.collector.db.execute("DELETE FROM meta WHERE key='done:10:1'")
            self.collector.discover()
            self.reply({"jobs": [dict(JOB, completed_at=None)]})
            self.collector.fetch_task()
        self.assertEqual(self.collector.health()["missing_completion"], 1)

    def test_non_204_not_acknowledged(self):
        self.enqueue()
        self.seal()
        self.collector.transport = lambda *args: (500, {}, b"")
        with self.assertRaises(RuntimeError):
            self.collector.flush()
        self.assertEqual(self.collector.health()["pending_events"], 1)

    def test_future_completion_omitted(self):
        self.enqueue(dict(JOB, completed_at="2026-10-22T00:00:00Z"))
        self.collector.flush()
        self.assertEqual(self.collector.health()["future_completion"], 1)

    def test_live_incomplete_does_not_hold_initial_history(self):
        self.reply({"workflow_runs": [RUN]})
        self.collector.discover()
        self.enqueue()
        self.reply({"jobs": [dict(JOB, id=21, status="in_progress")]})
        self.collector.fetch_task()
        self.assertTrue(self.collector.initial_ready())
        self.collector.flush()
        self.assertEqual(self.collector.health()["sent_events"], 1)

    def test_expiry_counted_even_behind_initial_barrier(self):
        self.enqueue()
        self.now += 8*c.DAY
        self.collector.flush()
        self.assertEqual(self.collector.health()["expired"], 1)

    def test_stale_run_refreshed_independent_of_discovery(self):
        self.reply({"workflow_runs": [dict(RUN, status="in_progress")]})
        self.collector.discover()
        self.reply({"status": "completed"})
        self.reply({"jobs": [JOB]})
        self.collector.fetch_task()
        self.assertIn("/runs/10/attempts/1", self.requests[1][0])
        self.assertEqual(self.collector.health()["pending_attempts"], 0)

    def test_github_failure_does_not_prevent_delivery(self):
        self.enqueue()
        self.seal()
        self.responses = [OSError("GitHub offline")]
        data = self.collector.cycle()
        self.assertEqual(data["sent_events"], 1)
        self.assertEqual(data["error"], "OSError")

    def test_terminal_deleted_attempt_does_not_starve(self):
        self.reply({"workflow_runs": [RUN, dict(RUN, id=11)]})
        self.collector.discover()
        self.responses.append((404, {}, b""))
        self.collector.fetch_task()
        self.reply({"jobs": [JOB]})
        self.collector.fetch_task()
        self.assertIn("/runs/11/attempts/1/jobs", self.requests[-1][0])
        self.assertEqual(self.collector.health()["unavailable_attempts"], 1)
        self.assertEqual(self.collector.health()["pending_attempts"], 0)

    def test_transient_attempt_does_not_starve(self):
        self.reply({"workflow_runs": [RUN, dict(RUN, id=11)]})
        self.collector.discover()
        self.responses.append(OSError("transient"))
        self.collector.fetch_task()
        self.reply({"jobs": [JOB]})
        self.collector.fetch_task()
        self.assertIn("/runs/11/attempts/1/jobs", self.requests[-1][0])
        self.assertEqual(self.collector.health()["api_failures"], 1)
        self.assertEqual(self.collector.health()["pending_attempts"], 1)

    def loki_reorder_transport(self, url, body=None):
        if body is None:
            return self.transport(url, body)
        if not hasattr(self, "watermarks"):
            self.watermarks = {}
        for stream in json.loads(body)["streams"]:
            key = json.dumps(stream["stream"], sort_keys=True)
            for ts, _ in stream["values"]:
                second = int(ts) // 10**9
                self.assertGreaterEqual(second, self.watermarks.get(key, second)-3600,
                                        "Loki would reject out-of-order sample")
                self.watermarks[key] = max(second, self.watermarks.get(key, second))
        return self.transport(url, body)

    def test_daily_scan_discovers_day_seven_rerun_of_completed_run(self):
        self.reply({"workflow_runs": [dict(RUN, updated_at=c.iso(self.now))]})
        self.collector.discover_reruns()
        self.reply({"jobs": [JOB]})
        self.collector.fetch_task()
        self.assertEqual(self.collector.health()["pending_attempts"], 0)
        self.now += 7*c.DAY
        self.reply({"workflow_runs": [dict(RUN, run_attempt=2,
                    created_at=c.iso(NOW-100*c.DAY), updated_at=c.iso(self.now))]})
        self.collector.discover_reruns()
        self.assertNotIn("created=", self.requests[-1][0])
        self.assertEqual(self.collector.db.execute("SELECT attempt FROM tasks").fetchone()[0], 2)
        self.reply({"jobs": [dict(JOB, id=21, started_at=c.iso(self.now-700), completed_at=c.iso(self.now-9))]})
        self.collector.fetch_task()
        self.collector.flush()
        self.assertEqual(self.collector.health()["sent_events"], 1)
        self.assertEqual(self.collector.health()["expired"], 1)  # prior undelivered attempt

    def test_rerun_scan_pagination_failure_and_restart(self):
        self.reply({"workflow_runs": [dict(RUN, id=i, updated_at=c.iso(self.now)) for i in range(100)]})
        self.collector.discover_reruns()
        self.assertEqual(self.collector.get("rerun_page"), "2")
        self.responses.append(OSError("offline"))
        with self.assertRaises(OSError):
            self.collector.discover_reruns()
        self.collector.db.close()
        self.collector = c.Collector(self.path, clock=lambda: self.now, transport=self.transport)
        self.reply({"workflow_runs": []})
        self.collector.discover_reruns()
        self.assertIn("page=2", self.requests[-1][0])
        self.assertEqual(self.collector.get("rerun_page"), "1")
        self.assertFalse(self.collector.discover_reruns())

    def test_fifth_older_completion_not_lost_across_four_task_cycle(self):
        self.collector.transport = self.loki_reorder_transport
        self.reply({"workflow_runs": [dict(RUN, id=i) for i in range(1, 6)]})
        self.reply({"workflow_runs": []})
        for i in range(4):
            self.reply({"jobs": [dict(JOB, id=20+i, started_at=c.iso(NOW-700), completed_at=c.iso(NOW-10))]})
        self.collector.cycle()
        self.assertEqual(self.collector.health()["sent_events"], 4)
        self.reply({"jobs": [dict(JOB, id=24, started_at=c.iso(NOW-4300), completed_at=c.iso(NOW-3610))]})
        self.collector.cycle()
        self.assertEqual(self.collector.health()["sent_events"], 5)
        self.assertEqual(self.collector.health()["out_of_order"], 0)

    def test_prolonged_500_does_not_block_initial_history_or_recovery(self):
        self.collector.transport = self.loki_reorder_transport
        self.reply({"workflow_runs": [RUN, dict(RUN, id=11)]})
        self.collector.discover()
        self.responses.append((500, {}, b""))
        self.collector.fetch_task()
        self.reply({"jobs": [dict(JOB, id=21, started_at=c.iso(NOW-700), completed_at=c.iso(NOW-10))]})
        self.collector.fetch_task()
        self.collector.flush()
        self.assertEqual(self.collector.health()["sent_events"], 1)
        for _ in range(12):
            self.now += 601
            self.responses.append((500, {}, b""))
            self.collector.fetch_task()
            self.collector.flush()
        self.assertEqual(self.collector.health()["sent_events"], 1)
        self.now += 601
        self.reply({"jobs": [JOB]})
        self.collector.fetch_task()
        self.collector.flush()
        self.assertEqual(self.collector.health()["sent_events"], 2)
        self.assertEqual(self.collector.health()["expired"], 0)

    def test_ambiguous_ack_multi_hour_retry_preserves_identity(self):
        self.enqueue()
        original_transport = self.loki_reorder_transport
        def ambiguous(url, body=None):
            original_transport(url, body)  # Backend received it; client lost the ack.
            raise OSError("ack lost")
        self.collector.transport = ambiguous
        with self.assertRaises(OSError):
            self.collector.flush()
        first = json.loads(self.requests[-1][1])["streams"][0]
        self.now += 3*3600
        self.collector.transport = original_transport
        self.enqueue(dict(JOB, id=21, started_at=c.iso(self.now-700), completed_at=c.iso(self.now-10)))
        self.collector.flush()
        matching = [json.loads(body)["streams"][0] for _, body in self.requests
                    if body and json.loads(body)["streams"][0]["stream"] == first["stream"]]
        self.assertEqual(matching, [first, first])
        self.assertEqual(self.collector.health()["sent_events"], 2)

    def test_completion_slot_wrap_and_old_generation_admission(self):
        self.collector.transport = self.loki_reorder_transport
        self.enqueue()
        self.collector.flush()
        self.now += 7*c.DAY
        self.enqueue(dict(JOB, id=21, started_at=c.iso(c.stamp(JOB["started_at"])+7*c.DAY),
                          completed_at=c.iso(c.stamp(JOB["completed_at"])+7*c.DAY)))
        self.collector.flush()
        self.assertEqual(self.collector.health()["sent_events"], 2)
        self.enqueue(dict(JOB, id=22))
        self.collector.flush()
        self.assertEqual(self.collector.health()["expired"], 1)
        self.assertEqual(len(self.requests), 2)

    def test_same_slot_reverse_arrival_is_inside_reorder_window(self):
        self.collector.transport = self.loki_reorder_transport
        for i, second in enumerate([1799, 0, 1000, 5]):
            end = (NOW//1800-1)*1800+second
            self.enqueue(dict(JOB, id=30+i, started_at=c.iso(end-100), completed_at=c.iso(end)))
            self.collector.flush()
        self.assertEqual(self.collector.health()["sent_events"], 4)
        self.assertEqual(len(self.watermarks), 1)

    def test_outbox_read_is_bounded_and_next_flush_progresses(self):
        for i in range(1001):
            self.enqueue(dict(JOB, id=100+i))
        self.collector.flush()
        self.assertEqual(self.collector.health()["sent_events"], 1000)
        self.collector.flush()
        self.assertEqual(self.collector.health()["sent_events"], 1001)

    def test_daily_discovery_does_not_expand_initial_backfill(self):
        old = dict(JOB, started_at=c.iso(NOW-6*c.DAY-100), completed_at=c.iso(NOW-6*c.DAY-1))
        self.enqueue(old)
        self.enqueue(old)
        self.collector.flush()
        self.assertEqual(self.collector.health()["outside_backfill"], 1)
        self.assertEqual(self.requests, [])

    def test_full_cycle_only_allowed_paths(self):
        self.reply({"workflow_runs": [RUN]})
        self.reply({"workflow_runs": []})
        self.reply({"jobs": [JOB]})
        health = self.collector.cycle()
        self.assertEqual(health["sent_events"], 1)
        self.assertEqual(health["pending_attempts"], 0)
        self.assertEqual(len(self.requests), 5)  # recent runs, daily scan, jobs, job push, status push
        self.assertEqual(json.loads(self.requests[-1][1])["streams"][0]["stream"]["event_type"], "collector_status")


if __name__ == "__main__":
    unittest.main(verbosity=2)
