#!/usr/bin/env python3
"""Count Terraform Enterprise workspaces per organisation, for licence reporting.

Read-only: issues GET requests only, against the customer's own TFE instance.

Usage:
    export TFE_HOST=tfe.example.com          # no scheme, no /api
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
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


INSECURE = False


def api(host, token, path):
    url = f"https://{host}/api/v2{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/vnd.api+json",
        },
    )
    ctx = ssl._create_unverified_context() if INSECURE else None
    with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
        return json.load(resp)


def paged(host, token, path):
    """Yield data items across all pages of a JSON:API list endpoint."""
    sep = "&" if "?" in path else "?"
    page = 1
    while True:
        body = api(host, token, f"{path}{sep}page%5Bnumber%5D={page}&page%5Bsize%5D=100")
        for item in body.get("data") or []:
            yield item
        pagination = (body.get("meta") or {}).get("pagination") or {}
        if page >= (pagination.get("total-pages") or page):
            return
        page += 1


def workspace_count(host, token, org):
    body = api(host, token, f"/organizations/{urllib.parse.quote(org)}/workspaces?page%5Bsize%5D=1")
    return ((body.get("meta") or {}).get("pagination") or {}).get("total-count", 0)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", default=os.environ.get("TFE_HOST", ""))
    p.add_argument("--token-file", default="")
    p.add_argument("--orgs", default="", help="comma-separated org names; skips the site-admin org listing")
    p.add_argument("--csv", default="", help="also write CSV to this path")
    p.add_argument(
        "--insecure",
        action="store_true",
        help="skip TLS verification (lab instances with self-signed certs only; "
        "for a corporate CA use SSL_CERT_FILE=/path/ca.pem instead)",
    )
    args = p.parse_args()
    global INSECURE
    INSECURE = args.insecure

    token = os.environ.get("TFE_TOKEN", "")
    if not token and args.token_file:
        with open(args.token_file) as fh:
            token = fh.read().strip()
    if not args.host or not token:
        sys.exit("error: set TFE_HOST and TFE_TOKEN (or --token-file)")

    try:
        if args.orgs:
            orgs = [o.strip() for o in args.orgs.split(",") if o.strip()]
        else:
            orgs = [o["attributes"]["name"] for o in paged(args.host, token, "/admin/organizations")]
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404) and not args.orgs:
            sys.exit(
                f"error: cannot list organisations as this token (HTTP {exc.code}). "
                "Use a site-admin token, or pass --orgs with the organisations you need."
            )
        raise

    captured = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    rows = []
    for org in sorted(orgs):
        try:
            rows.append((org, workspace_count(args.host, token, org)))
        except urllib.error.HTTPError as exc:
            rows.append((org, f"error HTTP {exc.code}"))

    total = sum(n for _, n in rows if isinstance(n, int))
    width = max([len(o) for o, _ in rows] + [12])

    print(f"TFE workspace count - {args.host} - {captured}")
    print(f"{'organisation':<{width}}  workspaces")
    print(f"{'-' * width}  ----------")
    for org, n in rows:
        print(f"{org:<{width}}  {n}")
    print(f"{'-' * width}  ----------")
    print(f"{'TOTAL':<{width}}  {total}")

    if args.csv:
        os.makedirs(os.path.dirname(args.csv) or ".", exist_ok=True)
        with open(args.csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["captured_utc", "host", "organisation", "workspaces"])
            for org, n in rows:
                w.writerow([captured, args.host, org, n])
            w.writerow([captured, args.host, "TOTAL", total])
        print(f"\nCSV written to {args.csv}")


if __name__ == "__main__":
    main()
