# PHASE 5 Final Acceptance — 2026-11-04

## Scope and environment

- Executed on `iMac-2.local` in the isolated worktree
  `/Volumes/OPP_ARCHIVE/O.P.P Studio/coincheck-ai-paperbot-security`.
- Docker context: `colima-coincheck-sec-test`; Compose project:
  `coincheck-ai-security`; frontend: `http://127.0.0.1:18098`.
- Verified commit: `eb8155145965024fe625ebe048b9ede5c10bd091`
  (`security/do-security-hardening-20261103`).
- Evidence collected 2026-11-04 12:33–12:54 JST (03:33–03:54 UTC).
- No DigitalOcean host, VPS service, firewall, SSH setting, normal Bot container,
  normal Bot database, or volume was changed.

## TASK 1 — image and running-code SHA

Result: **PASS**.

The isolated `.env` had retained the prior `APP_GIT_SHA`; it was updated locally
to the verified commit before building. The Dockerfile was then rebuilt with
`--no-cache` for `api`, using that exact build argument. `market` and `news`
were rebuilt from the same exact Dockerfile, build argument, and freshly built
source layers. Only `api`, `market`, and `news` were recreated.

| Evidence | Value |
| --- | --- |
| API / market / news image ID | `sha256:f7428dab08200902ec1582fb6795732792f6930a1320d12208c48a8b4b8a3421` |
| Local image digest | `coincheck-ai-security-api@sha256:f7428dab08200902ec1582fb6795732792f6930a1320d12208c48a8b4b8a3421` |
| OCI revision, all three images and running containers | `eb8155145965024fe625ebe048b9ede5c10bd091` |
| `APP_GIT_SHA`, all three running containers | `eb8155145965024fe625ebe048b9ede5c10bd091` |
| API `runtime.git_sha` (HTTP) | `eb8155145965024fe625ebe048b9ede5c10bd091` |
| Worktree / running-container `app/**/*.py` + `migrations/**/*.sql` aggregate SHA-256 | `d1c502d1a294c0221510727201e69057684058910efb0b8e47e41514b3ffaa1b` (equal) |

`api` and `db` were healthy after recreation; `market` and `news` were running.
The frontend returned HTTP 200. The normal Bot's five services remained up,
including its API and DB health checks.

## TASK 2 — encrypted backup restore

Result: **PASS for the newly generated backup and restore drill; the two legacy
archives remain unavailable for restore.**

Both existing age archives passed SHA-256 verification:

- `paperbot_20261103T021718Z.dump.age`
- `paperbot_20261103T023414Z.dump.age`

The two legacy archives remain checksum-valid, but their corresponding approved
project-external identity was not available. The legacy identity found inside
the project tree was deliberately neither used nor moved. Therefore neither
legacy archive has a successful restore claim.

A new age identity was generated outside the project and outside Docker at an
owner-approved, mode-700 user configuration location; the identity file is
mode 600. Its public recipient alone was installed on the isolated backup
generator (also mode 600). The recipient's non-secret SHA-256 identifier is
`a2824a99631ef5b04e81d98f429b884048243ca0fac0574d81fc3ad3eae0ad1e`.
No identity value was printed, committed, copied into the image, or stored in
the project.

The new archive `paperbot_20261104T053119Z.dump.age` was streamed from the
isolated DB, is 630,315 bytes, took 1 second to create, and passed its SHA-256
check. It was restored via `scripts/restore_clone.sh` in 2 seconds to the new,
previously absent `paperbot_restore_phase5_20261104` database, with no plaintext
dump written. The restore script reported 2 portfolios.

At verification, portfolio balances, BTC quantities, entry cost, realized P&L,
and bracket values matched exactly: both `baseline` and `news_gate` had
100,000 JPY cash, zero BTC, zero entry cost, zero realized P&L, and null
entry/SL/TP values. Snapshot counts also matched for portfolios (2), candles
(22), decisions (2), fills (0), news assessments (54), admin audits (18), and
schema migrations (1). The clone contained 38,494 market ticks; the live
isolated source had increased to 38,551 by comparison time because its public
collector continued running after the dump. This is expected snapshot drift,
not a restore discrepancy.

The restored clone passed the explicit migration and `app.security_e2e` suite:
role denials, audited pause, no replay after resume, stop-ack race handling,
portfolio preservation, permission rollback/re-apply, and clone E2E all
passed. `pg_amcheck` was not claimed because the installed PostgreSQL instance
does not have the `amcheck` extension; it was not installed on the running DB.

Recovery procedure: the owner supplies only the external identity *path* as
`BACKUP_IDENTITY_FILE` to `scripts/restore_clone.sh`; the script checks the
archive checksum before streaming age decryption to a new
`paperbot_restore_*` database. A second, independent disaster-recovery copy of
the identity has not been created by this task and remains an operational
follow-up. The project-resident legacy identity should be retired separately
under an approved key-rotation process.

## TASK 3 — HTTP pause persistence across API restart

Result: **PASS**.

The original pause state was `false`. Using the authorized admin token without
printing it, the isolated API accepted `POST /api/control/pause` with HTTP 200
and recorded an audit entry (`previous.paused=false`, `next_state.paused=true`).
Only `coincheck-ai-security-api-1` was restarted. After it became healthy,
authenticated `GET /api/status` returned HTTP 200 and `pause_new_buys=true`.
The matching audit record remained present. Paper-fill count remained `0`
throughout the check. The original `false` state was then restored through the
same HTTP endpoint; the restoration audit record was present and the final
HTTP readback returned `false`.

A short frontend 502 occurred immediately after the API restart while the
upstream was starting. It was not treated as a successful readback; the
verification was performed only after API health was `healthy` and returned
HTTP 200.

## TASK 4 — regression checks

Result: **PASS for the isolated regression suite**.

Completed checks:

| Check | Result |
| --- | --- |
| Python unit suite in the exact rebuilt image, tests mounted read-only | `Ran 32 tests` — PASS |
| Secret scan, including reachable Git history | PASS (`inspected=119`) |
| Isolated Compose parse | PASS |
| API authentication | unauthenticated 401; view-token control POST 403; admin status GET 200 |
| DB role boundary, read-only inspection | `paper_control`, `paper_market`, `paper_news`, `paper_query` exist; direct portfolio UPDATE is false for query/control/market |
| Runtime safeguards | HTTP runtime SHA above; `DRY_RUN_ONLY`; no private Coincheck endpoint or paid OpenAI call executed |
| Current isolated DB observations | 2 portfolios, 2 decisions, 0 fills, final pause state `false`; live market-tick count is intentionally time-varying |
| Restored-clone E2E | role boundary, pause race, no replay, portfolio preservation, permission rollback/re-apply — PASS |
| Disposable DB fixture | 13,000 JPY inclusive cap, unknown-news block, ATR exit while paused, RSI replay prevention, trade-ID dedupe, concurrent single BUY — PASS |

The mutation-heavy fixture was run only against a newly created
`paperbot_test_*` disposable DB, never against the normal Bot, the isolated
runtime DB, or the restored acceptance clone.

## Normal Bot protection

Result: **PASS**. The separate `colima-coincheck-ai` context still had all five
normal-Bot services up: frontend, API (healthy), market, news, and DB (healthy).
No command targeted its compose project, database, or volume.

## GitHub and release decision

This document is the only tracked acceptance artifact added by this run. It
does not contain tokens, credentials, private keys, or backup plaintext.

GitHub update result: the commit containing this document records the local
acceptance evidence; push and CI status are reported in the operator handoff.

The new approved recovery chain is proven, but the two legacy archives have no
currently approved restore identity. Consequently, **PHASE 5 is PASS for the
new recovery chain, with legacy-archive recovery BLOCKED**. This task does not
authorize a DigitalOcean deployment; deployment should remain on hold until
the legacy-archive retention/disposition and an independent key-recovery copy
are formally resolved.
