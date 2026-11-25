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

MAX_PAGES = 500
RETRIES = 3


def normalise_host(host: str) -> str:
    host = host.strip()
    for scheme in ("https://", "http://"):
        if host.startswith(scheme):
            host = host[len(scheme):]
    return host.rstrip("/")


def api(host, token, path, ctx, page=None):
    """GET one API path. Retries 429/5xx honouring Retry-After."""
    sep = "&" if "?" in path else "?"
    url = f"https://{host}/api/v2{path}"
    if page:
        url += f"{sep}page%5Bnumber%5D={page}&page%5Bsize%5D=100"
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
            time.sleep(int(exc.headers.get("Retry-After") or 2 ** attempt))


def paged(host, token, path, ctx):
    """Yield data items across all pages of a JSON:API list endpoint."""
    for page in range(1, MAX_PAGES + 1):
        body = api(host, token, path, ctx, page=page)
        items = body.get("data") or []
        yield from items
        total_pages = (body.get("meta") or {}).get("pagination", {}).get("total-pages")
        if not items or page >= (total_pages or page):
            return


def workspace_count(host, token, org, ctx):
    body = api(host, token, f"/organizations/{urllib.parse.quote(org, safe='')}/workspaces", ctx, page=1)
    count = ((body.get("meta") or {}).get("pagination") or {}).get("total-count")
    if count is None:
        raise ValueError("response has no meta.pagination.total-count")
    return count


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
    token = os.environ.get("TFE_TOKEN", "")
    if args.token_file:
        try:
            with open(args.token_file) as fh:
                token = fh.read().strip()
        except OSError as exc:
            sys.exit(f"error: cannot read token file: {exc}")
    if not host or not token:
        sys.exit("error: set TFE_HOST and TFE_TOKEN (or --token-file)")

    ctx = ssl._create_unverified_context() if args.insecure else None

    if args.orgs:
        orgs = sorted({o.strip() for o in args.orgs.split(",") if o.strip()})
    else:
        try:
            orgs = sorted({o["attributes"]["name"] for o in paged(host, token, "/admin/organizations", ctx)})
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404):
                sys.exit(
                    f"error: cannot list organisations with this token (HTTP {exc.code}). "
                    "Use a site-admin token, or pass --orgs with the organisations you need."
                )
            raise

    captured = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    rows = []
    for org in orgs:
        try:
            rows.append((org, workspace_count(host, token, org, ctx)))
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as exc:
            reason = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else str(exc)
            rows.append((org, None, reason))

    failed = [r for r in rows if r[1] is None]
    total = sum(r[1] for r in rows if r[1] is not None)
    width = max([len(r[0]) for r in rows] + [12])

    print(f"TFE workspace count - {host} - {captured}")
    print(f"{'organisation':<{width}}  workspaces")
    print(f"{'-' * width}  ----------")
    for row in rows:
        if row[1] is None:
            print(f"{row[0]:<{width}}  error ({row[2]})")
        else:
            print(f"{row[0]:<{width}}  {row[1]}")
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
                w.writerow([captured, host, row[0], row[1] if row[1] is not None else ""])
            w.writerow([captured, host, "TOTAL", "INCOMPLETE" if failed else total])
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
