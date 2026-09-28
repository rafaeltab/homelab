import importlib.util
import io
import json
import unittest
from datetime import date
from unittest.mock import patch
from pathlib import Path

spec = importlib.util.spec_from_file_location("exporter", Path(__file__).with_name("exporter.py"))
assert spec is not None and spec.loader is not None
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class Response:
    def __init__(self, rows, link=""):
        self.data = io.BytesIO(json.dumps(rows).encode())
        self.headers = {"Link": link}

    def read(self, size=-1):
        return self.data.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.data.close()


class ExporterTest(unittest.TestCase):
    def test_pages_count_merge_dates_not_closed_dates(self):
        urls = []
        def open_url(request, timeout):
            urls.append(request.full_url)
            return Response([{"merged_at": "2026-09-27T23:59:00Z"}, {"merged_at": None}], '<https://api.github.com/repos/rafaeltab/wallpaperdb/pulls?page=2>; rel="next"') if len(urls) == 1 else Response([{"merged_at": "2026-09-28T00:01:00Z"}])
        self.assertEqual(exporter.fetch_counts(open_url), {"2026-09-27": 1, "2026-09-28": 1})
        self.assertEqual(len(urls), 2)

    def test_metrics_include_zero_days_through_today(self):
        with patch.object(exporter, "counts", {"2026-09-27": 2, "2026-09-29": 1}):
            output = exporter.metrics(today=date(2026, 9, 30)).decode()
        self.assertIn('wallpaperdb_pr_merges_daily{day="2026-09-27"} 2\n', output)
        self.assertIn('wallpaperdb_pr_merges_daily{day="2026-09-28"} 0\n', output)
        self.assertIn('wallpaperdb_pr_merges_daily{day="2026-09-29"} 1\n', output)
        self.assertIn('wallpaperdb_pr_merges_daily{day="2026-09-30"} 0\n', output)
        self.assertNotIn('day="2026-09-26"', output)

    def test_error_does_not_publish_partial_page(self):
        def open_url(request, timeout):
            return Response([{"merged_at": "2026-09-27T23:59:00Z"}], '<https://example.com/evil>; rel="next"')
        with self.assertRaises(ValueError):
            exporter.fetch_counts(open_url)


if __name__ == "__main__":
    unittest.main()
