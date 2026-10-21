"""Regression tests for outages that previously left no usable evidence."""
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('observer', Path(__file__).with_name('observer.py'))
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


class ObserverTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = {'ip': '192.0.2.1', 'ssh_host': 'example', 'kubectl': 'kubectl',
                       'kubeconfig': '/unused', 'grafana_url': 'https://example/api/health',
                       'log_dir': self.temp.name + '/logs', 'state_dir': self.temp.name + '/state'}

    def replies(self, stale=False, ssh_ok=True):
        stamps = [{'metric': {'host_name': host}, 'value': [time.time(), str(time.time() - (400 if stale else 20))]}
                  for host in ('zerotwo', 'homelab1')]
        return {
            'ping': {'ok': True}, 'ssh': {'ok': ssh_ok},
            'kubernetes': {'ok': True, 'stdout': 'ok'},
            'grafana': {'ok': True, 'stdout': '{"database":"ok"}'},
            'telemetry': {'ok': True, 'stdout': 'true'},
            'metrics_freshness': {'ok': True, 'stdout': json.dumps({'data': {'result': stamps}})},
        }

    def do_probe(self, replies):
        with patch.object(observer, 'commands', return_value={k: [k] for k in replies}), \
             patch.object(observer, 'run', side_effect=lambda argv: replies[argv[0]]), \
             patch.object(observer, 'capture', return_value={'path': '/test/evidence'}) as capture:
            record = observer.probe(self.config)
            return record, capture.call_count

    def test_stale_metrics_capture_even_when_grafana_and_collector_are_healthy(self):
        record, captures = self.do_probe(self.replies(stale=True))
        self.assertFalse(record['healthy'])
        self.assertEqual(captures, 1)
        self.assertFalse(record['probes']['metrics_freshness']['ok'])
        self.assertTrue(record['probes']['grafana']['ok'])
        self.assertTrue(Path(self.config['log_dir'], 'probes/checks.jsonl').exists())

    def test_continuing_failure_is_saved_but_does_not_capture_every_thirty_seconds(self):
        self.do_probe(self.replies(ssh_ok=False))
        record, captures = self.do_probe(self.replies(ssh_ok=False))
        self.assertFalse(record['healthy'])
        self.assertEqual(captures, 0)
        rows = Path(self.config['log_dir'], 'probes/checks.jsonl').read_text().splitlines()
        self.assertEqual(len(rows), 2)

    def test_all_probes_run_after_an_individual_timeout(self):
        replies = self.replies()
        replies['ssh'] = {'ok': False, 'error': 'TimeoutExpired'}
        record, captures = self.do_probe(replies)
        self.assertEqual(len(record['probes']), 6)
        self.assertTrue(record['probes']['metrics_freshness']['ok'])
        self.assertEqual(captures, 1)

    def test_timed_out_snapshot_keeps_output_written_before_the_hang(self):
        result = observer.run([sys.executable, '-c',
            'import time; print("saved before hang", flush=True); time.sleep(10)'], timeout=0.2)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'TimeoutExpired')
        self.assertIn('saved before hang', result['stdout'])

    def test_log_rotation_retains_latest_data_without_unbounded_files(self):
        path = Path(self.temp.name) / 'archive.log'
        for n in range(10):
            observer.append(path, str(n) * 10, limit=15, backups=2)
        self.assertEqual(path.read_text(), '9' * 10)
        self.assertEqual(path.with_name('archive.log.1').read_text(), '8' * 10)
        self.assertEqual(len(list(path.parent.glob('archive.log*'))), 3)


if __name__ == '__main__':
    unittest.main()
