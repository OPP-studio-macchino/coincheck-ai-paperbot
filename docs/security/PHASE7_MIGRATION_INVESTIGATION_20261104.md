# PHASE 7 — fresh-VPS migration investigation (2026-11-04)

## Result

The VPS root cause is **not confirmed**.  Its prior acceptance record exposed
only `MIGRATION_FAILED category=RuntimeError`; that deliberately redacted line
cannot distinguish configuration, mounted-secret, database, source-schema, or
SQL-upgrade failure.  This environment could not make the authorized
non-privileged SSH read: connection to port 22 was blocked locally with
`Operation not permitted` before authentication.

The local Docker socket was likewise unavailable (`permission denied` on the
specified Colima context).  Therefore no new container, project, or volume was
created here, and no local fresh-volume migration result is claimed.

## Confirmed local repair

`app/migrate.py` now emits only a redacted structured marker on failure:

```
MIGRATION_FAILED stage=<settings|secret_validation|db_connection|source_schema|upgrade> category=<exception-class> [sqlstate=<five-character-code>]
```

It never prints an exception message, SQL text, or a secret.  SQLSTATE is
included only when it is exactly five alphanumeric characters.  The migration
validates all role-secret files before starting its transaction; a missing or
invalid mounted file is therefore unambiguously `secret_validation`.  A
missing one of the nine base tables is `source_schema`; transaction/DDL errors
remain `upgrade` with an optional safe SQLSTATE.

This is diagnostic-only: it does not reset schemas, balances, or volumes.

## Evidence run locally

- `python3 -B -m unittest discover -s tests -q` — PASS (33 tests)
- `python3 -B scripts/scan_secrets.py --history` — PASS (`inspected=125`)
- `DOCKER_CONTEXT=colima-coincheck-sec-test docker-compose -p coincheck-migrate-phase7-20261104-a --profile migration config --quiet` — PASS
- Fresh isolated migration — BLOCKED before creation: Docker socket permission
  denied.  The disposable source directory was created under `/private/tmp`,
  but Docker did not create a project or volume.

## Safe owner-run next action

The old VPS image `coincheck-vps-candidate:2c43f17` is **stale**: it cannot
emit the stage marker.  Build a new linux/amd64 candidate from the repaired
full Git SHA, then run it once against a **new explicitly named** Compose
project and new volume.  Wait for the DB health check before running only the
`migration` service and preserve its stdout/stderr marker.

Stop immediately on any `MIGRATION_FAILED` marker.  Do not retry against the
same failed volume, do not run `down -v`, and do not reset or initialize the
preserved VPS volume.  The marker determines the bounded follow-up:

- `settings` — compare only non-secret runtime flags (`DRY_RUN`, live-trading
  flag, `NEWS_MODE`, database name, and full SHA).
- `secret_validation` — check secret-file existence, regular-file type, mount
  target, size, and permissions; never print contents.
- `db_connection` — collect only category/SQLSTATE plus DB health state.
- `source_schema` — inventory table names/count only; do not initialize the
  preserved volume.
- `upgrade` — retain category/SQLSTATE and stop for reviewed SQL diagnosis.

The staged `phase7-cryptofx-resume.sh` was not executed or inspected in this
task because the VPS connection was unavailable and this task permits only
non-privileged read-only VPS checks.
