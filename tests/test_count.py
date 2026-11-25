#!/usr/bin/env python3
"""Self-check for tfe-workspace-count.py. Stdlib only.

    python3 -m unittest discover -s tests -v
"""

import importlib.util
import io
import json
import pathlib
import sys
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "tfe-workspace-count.py"
spec = importlib.util.spec_from_file_location("twc", SCRIPT)
twc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(twc)

RESPONSES = {}  # API path -> envelope dict, or an exception instance to raise


def envelope(data, total_count=None, total_pages=1):
    pagination = {"total-pages": total_pages}
    if total_count is not None:
        pagination["total-count"] = total_count
    return {"data": data, "meta": {"pagination": pagination}}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def run(argv, env=None):
    paths = []

    def fake_urlopen(req, timeout=None, context=None):
        path = req.full_url.split("/api/v2", 1)[1]
        paths.append(path)
        reply = RESPONSES.get(path)
        if isinstance(reply, Exception):
            raise reply
        if reply is None:
            raise AssertionError(f"unexpected request: {path}")
        return FakeResponse(json.dumps(reply).encode())

    with mock.patch.object(twc.urllib.request, "urlopen", fake_urlopen), \
            mock.patch.object(sys, "argv", ["tfe-workspace-count.py"] + argv), \
            mock.patch.dict("os.environ", env or {"TFE_HOST": "tfe.example.com", "TFE_TOKEN": "tok"}, clear=True), \
            redirect_stdout(out := io.StringIO()), redirect_stderr(err := io.StringIO()):
        try:
            twc.main()
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue(), paths


def org_path(org):
    return f"/organizations/{org}/workspaces?page%5Bnumber%5D=1&page%5Bsize%5D=1"


class Tests(unittest.TestCase):
    def setUp(self):
        RESPONSES.clear()

    def test_happy_path_counts_with_one_record_per_org(self):
        RESPONSES[org_path("finance")] = envelope([], 1820)
        code, out, err, paths = run(["--orgs", "finance"])
        self.assertEqual(code, 0)
        self.assertIn("TOTAL         1820", out)
        self.assertIn("page%5Bsize%5D=1", paths[0])
        self.assertEqual(err, "")

    def test_partial_failure_is_incomplete_and_exits_2(self):
        RESPONSES[org_path("finance")] = envelope([], 10)
        RESPONSES[org_path("platform")] = urllib.error.HTTPError(
            "/organizations/platform/workspaces", 403, "forbidden", {}, None
        )
        code, out, err, _ = run(["--orgs", "finance,platform"])
        self.assertEqual(code, 2)
        self.assertIn("INCOMPLETE - 1 organisation(s) failed", out)
        self.assertIn("not usable as a licence figure", err)

    def test_duplicate_orgs_are_counted_once(self):
        RESPONSES[org_path("finance")] = envelope([], 7)
        code, out, _, paths = run(["--orgs", "finance,finance,finance"])
        self.assertEqual(code, 0)
        self.assertEqual(len(paths), 1)
        self.assertIn("TOTAL         7", out)

    def test_missing_total_count_is_an_error_not_zero(self):
        RESPONSES[org_path("finance")] = envelope([], None)
        code, out, _, _ = run(["--orgs", "finance"])
        self.assertEqual(code, 2)
        self.assertIn("no meta.pagination.total-count", out)

    def test_truncated_org_list_refuses_to_report_a_total(self):
        RESPONSES["/admin/organizations?page%5Bnumber%5D=1&page%5Bsize%5D=100"] = envelope(
            [{"attributes": {"name": "finance"}}], total_count=250
        )
        code, _, err, _ = run([])
        self.assertEqual(code, 1)
        self.assertIn("truncated", err)

    def test_null_pagination_is_not_a_crash(self):
        # A null pagination block means completeness cannot be confirmed: refuse, don't guess.
        RESPONSES["/admin/organizations?page%5Bnumber%5D=1&page%5Bsize%5D=100"] = {
            "data": [{"attributes": {"name": "finance"}}],
            "meta": {"pagination": None},
        }
        code, out, err, _ = run([])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("completeness cannot be confirmed", err)

    def test_empty_orgs_argument_fails(self):
        code, _, err, _ = run(["--orgs", " , "])
        self.assertEqual(code, 1)
        self.assertIn("no organisation names", err)

    def test_missing_token_fails_without_a_table(self):
        code, out, err, _ = run(["--orgs", "finance"], env={"TFE_HOST": "tfe.example.com", "TFE_TOKEN": ""})
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("set TFE_HOST and TFE_TOKEN", err)

    def test_date_form_retry_after_falls_back_to_backoff(self):
        headers = {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
        exc = urllib.error.HTTPError("u", 429, "slow down", headers, None)
        self.assertEqual(twc.retry_seconds(exc, 1), 2)
        self.assertEqual(twc.retry_seconds(exc, 3), 8)
        self.assertEqual(twc.retry_seconds(urllib.error.HTTPError("u", 429, "x", {"Retry-After": "7"}, None), 1), 7)

    def test_non_json_success_on_org_listing_reports_cleanly(self):
        RESPONSES["/admin/organizations?page%5Bnumber%5D=1&page%5Bsize%5D=100"] = json.JSONDecodeError("boom", "", 0)
        code, _, err, _ = run([])
        self.assertEqual(code, 1)
        self.assertIn("cannot list organisations", err)

    def test_org_listing_without_pagination_metadata_is_not_assumed_complete(self):
        RESPONSES["/admin/organizations?page%5Bnumber%5D=1&page%5Bsize%5D=100"] = {
            "data": [{"attributes": {"name": "finance"}}]
        }
        code, _, err, _ = run([])
        self.assertEqual(code, 1)
        self.assertIn("completeness cannot be confirmed", err)

    def test_empty_token_file_is_reported_as_such(self):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".tok", delete=False) as fh:
            fh.write("   \n")
            path = fh.name
        code, _, err, _ = run(["--token-file", path])
        self.assertEqual(code, 1)
        self.assertIn("token file is empty", err)

    def test_host_with_scheme_and_trailing_slash_is_normalised(self):
        RESPONSES[org_path("finance")] = envelope([], 1)
        code, out, _, _ = run(["--orgs", "finance"],
                              env={"TFE_HOST": "https://tfe.example.com/", "TFE_TOKEN": "tok"})
        self.assertEqual(code, 0)
        self.assertIn("tfe.example.com -", out)


if __name__ == "__main__":
    unittest.main()
