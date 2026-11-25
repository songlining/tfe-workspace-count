#!/usr/bin/env python3
"""Count Terraform Enterprise workspaces per organisation, for licence reporting.

Read-only: issues GET requests only, against the customer's own TFE instance.

Usage:
    export TFE_HOST=tfe.example.com          # hostname only; https:// and a trailing / are stripped
    export TFE_TOKEN=<site-admin token>      # or pass --token-file
    ./tfe-workspace-count.py                  # all orgs, table to stdout
    ./tfe-workspace-count.py --csv report.csv
    ./tfe-workspace-count.py --orgs finance,platform   # no site-admin token

Requires: python3 (stdlib only). No curl, no jq.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import NamedTuple

MAX_PAGES = 500
RETRIES = 3


class Row(NamedTuple):
    org: str
    count: int | None = None
    reason: str = ""


class ListingTruncated(Exception):
    pass


def fail(message):
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def normalise_host(host: str) -> str:
    host = host.strip()
    lowered = host.lower()
    for scheme in ("https://", "http://"):
        if lowered.startswith(scheme):
            host = host[len(scheme):]
    return host.rstrip("/")


def retry_seconds(exc, attempt):
    header = exc.headers.get("Retry-After") if exc.headers else None
    try:
        return int(header)
    except (TypeError, ValueError):
        return 2 ** attempt  # also covers date-form Retry-After


def api(host, token, path, ctx, page=None, size=100):
    """GET one API path. Retries 429/5xx, honouring a numeric Retry-After."""
    sep = "&" if "?" in path else "?"
    url = f"https://{host}/api/v2{path}"
    if page:
        url += f"{sep}page%5Bnumber%5D={page}&page%5Bsize%5D={size}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/vnd.api+json",
        },
    )
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 and exc.code < 500:
                raise
            if attempt == RETRIES - 1:
                raise
            time.sleep(retry_seconds(exc, attempt))


def paged(host, token, path, ctx):
    """Yield data items across all pages, and fail if fewer arrived than the API promised."""
    expected = None
    received = 0
    for page in range(1, MAX_PAGES + 1):
        body = api(host, token, path, ctx, page=page)
        items = body.get("data") or []
        pagination = (body.get("meta") or {}).get("pagination") or {}
        if expected is None:
            expected = pagination.get("total-count")
        yield from items
        received += len(items)
        if not items or page >= (pagination.get("total-pages") or page):
            break
    if isinstance(expected, int) and received < expected:
        raise ListingTruncated(f"received {received} of {expected} records")


def workspace_count(host, token, org, ctx):
    org_path = urllib.parse.quote(org, safe="")
    body = api(host, token, f"/organizations/{org_path}/workspaces", ctx, page=1, size=1)
    count = ((body.get("meta") or {}).get("pagination") or {}).get("total-count")
    if not isinstance(count, int):
        raise ValueError("response has no meta.pagination.total-count")
    return count


def read_token(path):
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            token = fh.read().strip()
    except OSError as exc:
        fail(f"cannot read token file: {exc}")
    if not token:
        fail(f"token file is empty: {path}")
    return token


def safe_cell(value):
    """Neutralise CSV formula injection for names that begin with = + - @."""
    text = "" if value is None else str(value)
    return f"'{text}" if text[:1] in ("=", "+", "-", "@") else text


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", default=os.environ.get("TFE_HOST", ""))
    p.add_argument("--token-file", default="", help="takes precedence over $TFE_TOKEN")
    p.add_argument("--orgs", default="", help="comma-separated org names; skips the site-admin org listing")
    p.add_argument("--csv", default="", help="also write CSV to this path")
    p.add_argument(
        "--insecure",
        action="store_true",
        help="skip TLS verification (lab instances with self-signed certs only; "
        "for a corporate CA use SSL_CERT_FILE=/path/ca.pem instead)",
    )
    args = p.parse_args()

    host = normalise_host(args.host)
    token = read_token(args.token_file) if args.token_file else os.environ.get("TFE_TOKEN", "").strip()
    if not host or not token:
        fail("set TFE_HOST and TFE_TOKEN (or --token-file)")

    ctx = ssl._create_unverified_context() if args.insecure else None

    if args.orgs:
        orgs = sorted({o.strip() for o in args.orgs.split(",") if o.strip()})
        if not orgs:
            fail("--orgs contained no organisation names")
    else:
        try:
            orgs = sorted({o["attributes"]["name"] for o in paged(host, token, "/admin/organizations", ctx)})
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404):
                fail(
                    f"cannot list organisations with this token (HTTP {exc.code}). "
                    "Use a site-admin token, or pass --orgs with the organisations you need."
                )
            raise
        except ListingTruncated as exc:
            fail(f"organisation list was truncated ({exc}); refusing to report a total.")
        except (urllib.error.URLError, ValueError) as exc:
            fail(f"cannot list organisations: {exc}")

    captured = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    rows = []
    for org in orgs:
        try:
            rows.append(Row(org, workspace_count(host, token, org, ctx)))
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as exc:
            reason = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else str(exc)
            rows.append(Row(org, None, reason))

    failed = [r for r in rows if r.count is None]
    total = sum(r.count for r in rows if r.count is not None)
    width = max([len(r.org) for r in rows] + [12])

    print(f"TFE workspace count - {host} - {captured}")
    print(f"{'organisation':<{width}}  workspaces")
    print(f"{'-' * width}  ----------")
    for row in rows:
        if row.count is None:
            print(f"{row.org:<{width}}  error ({row.reason})")
        else:
            print(f"{row.org:<{width}}  {row.count}")
    print(f"{'-' * width}  ----------")
    if failed:
        print(f"{'TOTAL':<{width}}  {total} (INCOMPLETE - {len(failed)} organisation(s) failed)")
    else:
        print(f"{'TOTAL':<{width}}  {total}")

    if args.csv:
        os.makedirs(os.path.dirname(args.csv) or ".", exist_ok=True)
        with open(args.csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["captured_utc", "host", "organisation", "workspaces"])
            for row in rows:
                w.writerow([captured, safe_cell(host), safe_cell(row.org), row.count if row.count is not None else ""])
            w.writerow([captured, safe_cell(host), "TOTAL", "INCOMPLETE" if failed else total])
        print(f"\nCSV written to {args.csv}")

    if failed:
        print(
            f"\nWARNING: {len(failed)} organisation(s) could not be counted; the total is "
            "a lower bound and not usable as a licence figure.",
            file=sys.stderr,
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
