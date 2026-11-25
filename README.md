# tfe-workspace-count

Count the workspaces in a Terraform Enterprise installation, per organisation, using only
`python3` and an API token. Built for licence reporting on self-managed TFE installs where
product usage reporting is not enabled, so the workspace count has to be captured manually
and reproducibly.

Read-only: every request is a `GET`, against the customer's own TFE instance.

```text
$ ./tfe-workspace-count.py
TFE workspace count - tfe.example.com - 2026-11-25 20:53:47Z
organisation  workspaces
------------  ----------
finance       1820
platform      2410
------------  ----------
TOTAL         4230
```

If any organisation fails (401, 404, network), the run says so rather than reporting a clean
total:

```text
finance       1820
platform      error (HTTP 403)
------------  ----------
TOTAL         1820 (INCOMPLETE - 1 organisation(s) failed)
```

The CSV writes an empty count for a failed organisation and `INCOMPLETE` in the total row, and
the script exits 2, so an incomplete capture cannot be mistaken for a licence figure.

## Why workspace count

Terraform Enterprise entitlements are expressed in workspaces, so the number worth reporting
is the count of workspaces that exist across the whole installation, captured on a fixed
cadence and kept as evidence for the reporting period. Resources under management (RUM) is a
separate consumption metric and not the same number.

Note what the number is: workspaces **visible to the token you run it with**, at the moment of
capture. A token that can only see part of an organisation returns a partial count with no
error, so use a site-admin token for a licence figure, or confirm the `--orgs` token holds Read
on every workspace in each organisation it is pointed at.

## Requirements

- `python3` (standard library only - no `curl`, no `jq`, no packages)
- A Terraform Enterprise API token:
  - a **site-admin** token for the whole-installation count, or
  - with `--orgs`, a token with Read on every workspace in each organisation you name

## Usage

```bash
export TFE_HOST=tfe.example.com        # hostname only, no scheme, no /api
export TFE_TOKEN=<token>               # or pass --token-file /path/to/token
./tfe-workspace-count.py --csv report.csv
```

| Flag | Meaning |
|------|---------|
| `--host` | TFE hostname (defaults to `$TFE_HOST`) |
| `--token-file` | read the token from a file - takes precedence over `$TFE_TOKEN` |
| `--orgs a,b,c` | count only these organisations; skips the site-admin org listing. Duplicates are ignored |
| `--csv PATH` | also write the counts to a CSV, one row per organisation plus a `TOTAL` row |
| `--insecure` | skip TLS verification - lab instances with self-signed certificates only |

For a corporate certificate authority, prefer `SSL_CERT_FILE=/path/to/ca.pem` over
`--insecure`.

## What it calls

```text
GET /api/v2/admin/organizations?page[number]=N&page[size]=100          (site-admin only)
GET /api/v2/organizations/<org>/workspaces?page[size]=1                -> meta.pagination.total-count
```

The workspace count comes from `meta.pagination.total-count`, so only one workspace record is
ever fetched per organisation. There is no state download, no plan/apply, and no mutation. Rate
limited (`429`) and `5xx` responses are retried up to three times, honouring `Retry-After`.

## Network footprint

- HTTPS to the TFE hostname you configure, and nothing else. No telemetry, no callbacks to
  HashiCorp, no outbound dependency beyond that host.
- The token is read from the environment or a file and is never written to the output. The
  CSV contains the capture timestamp, hostname, organisation names and counts only.
- The count is point-in-time: workspaces that exist at the moment of capture.

## Self-checks

Thirteen stdlib-only tests cover the cases that would otherwise produce a wrong licence number:
incomplete runs, a missing `total-count`, duplicate or empty `--orgs`, a truncated organisation
list, pagination metadata that cannot confirm completeness, a non-JSON or non-object response on
the organisation listing, date-form `Retry-After`, an empty token file, and host normalisation.

Two deliberate refusals are worth knowing: if any organisation fails to count, the run exits 2
and the CSV total row says `INCOMPLETE`; and if the organisation list cannot be shown to be
complete (no `meta.pagination.total-count`, or fewer records than it promised), the run exits 1
without a total, because a low licence figure is worse than no figure.

```bash
python3 -m unittest discover -s tests -v
```

## Using tfctl instead

If the [`tfctl`](https://github.com/hashicorp/tfctl-cli) CLI is available or already approved,
the same numbers come from two calls:

```bash
tfctl api /admin/workspaces --page-size 1 --jq '.meta.pagination["total-count"]'
tfctl api /admin/organizations --page-size 100 --jq '[.data[].attributes.name]'
```

See [`songlining/tfc-rum-audit`](https://github.com/songlining/tfc-rum-audit) for a companion
tool that reports billable RUM by provider and resource type.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | every organisation counted |
| 1 | missing configuration or unreadable token file, or the token cannot list organisations and no `--orgs` was given |
| 2 | at least one organisation failed, so the total is a lower bound and not usable as a licence figure |

## Licence

MIT. See [LICENSE](LICENSE).
