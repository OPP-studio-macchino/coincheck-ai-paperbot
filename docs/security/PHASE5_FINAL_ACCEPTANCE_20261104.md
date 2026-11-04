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

Result: **BLOCKED**.

Both existing age archives passed SHA-256 verification:

- `paperbot_20261103T021718Z.dump.age`
- `paperbot_20261103T023414Z.dump.age`

The restore script correctly requires an identity outside the project tree. No
approved, separate offline identity was available for this acceptance run. An
identity file found inside the project tree was deliberately not used or moved:
doing so would violate the script's protection and the acceptance safety rules.
No archive was decrypted, no `paperbot_restore_*` database was created, and no
plaintext dump was written. Therefore no restore duration, clone-table counts,
or clone portfolio reconciliation can be claimed.

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

Result: **BLOCKED** (because TASK 2 has not completed; no false overall pass).

Completed checks:

| Check | Result |
| --- | --- |
| Python unit suite in the exact rebuilt image, tests mounted read-only | `Ran 32 tests` — PASS |
| Secret scan, including reachable Git history | PASS (`inspected=119`) |
| Isolated Compose parse | PASS |
| API authentication | unauthenticated 401; view-token control POST 403; admin status GET 200 |
| DB role boundary, read-only inspection | `paper_control`, `paper_market`, `paper_news`, `paper_query` exist; direct portfolio UPDATE is false for query/control/market |
| Runtime safeguards | HTTP runtime SHA above; `DRY_RUN_ONLY`; no private Coincheck endpoint or paid OpenAI call executed |
| Current isolated DB observations | 2 portfolios, 33,509 market ticks, 2 decisions, 0 fills, 18 audit rows, final pause state `false` |

The mutation-heavy clone fixture tests were not rerun against the already-used
isolated database because they intentionally alter paper-fixture data. Their
successful execution requires a disposable clone, which is part of the blocked
encrypted restore drill.

## Normal Bot protection

Result: **PASS**. The separate `colima-coincheck-ai` context still had all five
normal-Bot services up: frontend, API (healthy), market, news, and DB (healthy).
No command targeted its compose project, database, or volume.

## GitHub and release decision

This document is the only tracked acceptance artifact added by this run. It
does not contain tokens, credentials, private keys, or backup plaintext.

GitHub update result: the commit containing this document records the local
acceptance evidence; push and CI status are reported in the operator handoff.

Residual material risk: encrypted backup restoration has not been proven with
an approved offline identity in a new clone database. Consequently, **PHASE 5
is BLOCKED** and DigitalOcean deployment preparation must not proceed yet.
