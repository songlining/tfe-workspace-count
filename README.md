# tfe-workspace-count

Count the workspaces in a Terraform Enterprise installation, per organisation, using only
`python3` and an API token. Built for licence reporting on self-managed TFE installs where
self-reporting (Census) is not enabled, so the workspace count has to be captured manually
and reproducibly.

Read-only: every request is a `GET`, against the customer's own TFE instance.

```text
$ ./tfe-workspace-count.py
TFE workspace count - tfe.example.com - 2026-11-25 20:53:47Z
organisation  workspaces
------------  ----------
finance               1820
platform              2410
------------  ----------
TOTAL                 4230
```

## Why workspace count

Terraform Enterprise entitlements are expressed in workspaces, so the number worth reporting
is the count of workspaces that exist across the whole installation, captured on a fixed
cadence and kept as evidence for the reporting period. Resources under management (RUM) is a
separate consumption metric and not the same number.

## Requirements

- `python3` (standard library only - no `curl`, no `jq`, no packages)
- A Terraform Enterprise API token:
  - a **site-admin** token for the whole-installation count, or
  - any token that can read the organisations you name with `--orgs`

## Usage

```bash
export TFE_HOST=tfe.example.com        # hostname only, no scheme, no /api
export TFE_TOKEN=<token>               # or pass --token-file /path/to/token
./tfe-workspace-count.py --csv report.csv
```

| Flag | Meaning |
|------|---------|
| `--host` | TFE hostname (defaults to `$TFE_HOST`) |
| `--token-file` | read the token from a file instead of `$TFE_TOKEN` |
| `--orgs a,b,c` | count only these organisations; skips the site-admin org listing |
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
ever fetched per organisation. There is no state download, no plan/apply, and no mutation.

## Network footprint

- HTTPS to the TFE hostname you configure, and nothing else. No telemetry, no callbacks to
  HashiCorp, no outbound dependency beyond that host.
- The token is read from the environment or a file and is never written to the output. The
  CSV contains the capture timestamp, hostname, organisation names and counts only.
- The count is point-in-time: workspaces that exist at the moment of capture.

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
| 0 | counts captured (per-organisation HTTP errors appear as `error HTTP <code>` rows) |
| non-zero | missing `TFE_HOST`/`TFE_TOKEN`, or the token cannot list organisations and no `--orgs` was given |

## Licence

MIT. See [LICENSE](LICENSE).
