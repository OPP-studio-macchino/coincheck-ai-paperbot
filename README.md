# Coincheck AI Paper Bot — security hardening candidate

**O.P.P Studio / BTC/JPY spot / DRY_RUN ONLY / security branch**
**No actual Coincheck account, no authenticated Coincheck endpoint, no live exchange orders.**
2026-11-03: This candidate is isolated from the working `main` Bot. It is **NOT deployed to any DigitalOcean Droplet**.

## Architecture

| Docker service | Role | Secrets | Host port |
|---|---|---|---|
| `db` | PostgreSQL durable ticks, 2h/4h candles, paper balances, news, decisions, audits | schema admin only | None |
| `migration` | Explicit versioned, non-destructive schema/role changes; profile-only | schema admin + role password files | None |
| `market` | Coincheck **PUBLIC** BTC/JPY trades and public order-book, strategy and paper exits | market DB role only | None |
| `news` | News risk assessment, default mock/unknown | news DB role only | None |
| `api` | Authenticated GET and audited POST pause/resume | query/control DB and view/admin tokens | None |
| `frontend` | Static Nginx UI and API reverse proxy | No credentials | `127.0.0.1:8097` (VPS planned), `18098` isolated |

The DB-only `db_private` and API-side `front_edge` networks are `internal:true`; market and news have restricted egress; frontend has a separate non-internal Docker transport solely for **127.0.0.1** host port publication. Do not expose DB or internal API ports to the host. Cloud Firewall, host listening and external reachability must be checked independently on any future VPS.

## Frozen strategy (exact specification)

- Decision on **confirmed 2-hour candles** only; EMA21 > EMA55, local close above EMA21, EMA55 rising versus three bars earlier.
- RSI9 crosses above **55**, Window3 = crossover bar plus next three confirmed 2h bars, remains above 55; deduplicate each RSI event.
- Higher timeframe: **previous confirmed 4-hour close > EMA50 > EMA200**; no future OHLCV.
- ADX and extra oscillator OFF. ATR14 fixed on entry: stop loss **3× ATR**, take profit **12× ATR**.
- Long-only, single position per virtual portfolio; `baseline` and `news_gate` are independent, each seeded with **100,000 JPY** on a genuinely new database only.
- New paper BUY inclusive of fees is capped by actual `BUY_BUDGET_JPY` (max **50,000 JPY**, can lower to 13,000). No automatic top-up/reset of current balances. Existing bracket prices do not change on new config.
- Minimum 0.001 BTC and 500 JPY value. No reliable historical Coincheck 2h/4h bar repair; collector conservatively requires continuous 200 confirmed 2h bars and 300 confirmed 4h bars before buying. A websocket gap triggers fail-closed history checks.
- `news_gate` blocks on mock/unknown/expired/unverified news; `baseline` is independent. News cannot initiate trades or override ATR exits. A model URL alone is not article verification.
- Public price stale/disconnected -> no BUY/SELL simulator fills, no misleading current valuation.

## Isolated macOS test environment

```bash
cd "/Volumes/OPP_ARCHIVE/O.P.P Studio/coincheck-ai-paperbot-security"
python3 scripts/bootstrap_env.py           # creates private secrets/ and .env
python3 -m unittest discover -s tests -q
python3 scripts/scan_secrets.py --history
docker-compose config -q
# On iMac, choose an isolated Colima context, NEVER the production profile:
export DOCKER_CONTEXT=colima-coincheck-sec-test
docker-compose up -d db
docker-compose --profile migration run --rm migration
docker-compose up -d --build
docker-compose ps
SECURITY_ISOLATED_TEST=YES_CLONE python3 -B scripts/smoke_isolated.py
```

The test project is `coincheck-ai-security`, local port **18098**, isolated Docker engine & volume. The existing main bot is `coincheck-ai-paperbot`, local port **8097**. **Never** run `docker compose down -v`, never target the live volume from the isolated context. Production database migration requires a separately reviewed clone and credential transition; the migration-only service is never part of ordinary startup.

In the test frontend open `http://127.0.0.1:18098`. Enter the **view token** for read-only access or the **admin token** only when you need to pause/resume new paper buys. The browser holds the token in volatile memory (not URL, not localStorage). The code offers no remote shell, arbitrary Python, arbitrary Git branch execution or Docker administration.

## Free macOS SSH tunnel for future DigitalOcean VPS

There is no mandatory paid VPN. Keep frontend bound only to VPS loopback `127.0.0.1:8097` and use:

```bash
ssh -N -T -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -o StrictHostKeyChecking=yes -i ~/.ssh/REAL_BOT_ADMIN_KEY \
  -L 127.0.0.1:18097:127.0.0.1:8097 bot-admin@VPS_IP
# Browse http://127.0.0.1:18097 on Mac while SSH is connected
```

The Bot runs independently of the browser/SSH tunnel. An iPhone/WireGuard interface may be added later, but VPN access alone does **not** expose a localhost-only service; it needs a separately authenticated VPN-address-limited proxy. Never switch this service to `0.0.0.0`.

## Security posture and admission gate

- `DRY_RUN=true`, `LIVE_TRADING_ENABLED=false`, `NEWS_MODE=mock`. Coincheck private credentials at startup cause rejection. No paid OpenAI news API or OpenAI key is needed for acceptance tests.
- Separate non-root Python and DB roles: market, news, query, control; the schema-admin credential exists only in PostgreSQL and explicit migration job. API contains both query and control connections, **not** complete process isolation.
- Additive migration `migrations/001_security_roles_audit_runtime.sql` retains existing balances and freezes paper entry brackets, while PostgreSQL advisory transaction locking serializes pause responses with paper BUY commits. Audit records include actor, UTC time, request ID, prior/next state, result, reason, without bearer tokens.
- Application services run read-only rootfs (DB data volume remains writable), non-root where appropriate, all capabilities dropped except minimum PostgreSQL setup, no-new-privileges and bounded PID/RAM/CPU. No privileged/Docker socket/host networking.
- GitHub workflow uses SHA-pinned checkout/setup-python actions, fail-closed Python tests, history-safe redacted secret scan, public dependency advisory scan, Compose security checks, and container image build. Python versions in `requirements.lock` are pinned. OCI base images and Compose images are digest-pinned.
- The view/admin token and database credentials are loaded from dedicated local files; Compose secrets are **mounts**, not a protection against host compromise. Keep secrets and backup decrypt keys separate from public code and from removable backups.
- Backups use streamed `pg_dump -Fc` into age encryption and SHA256, with offsite copying and restoration tests required before VPS production use.

**Health 200 is not trading-ready.** The dashboard reports effective strategy/Git SHA/budget, real-assets-unconnected status, latest market time, confirmed contiguous bar counts and shortfall reason, exit-evaluation availability, and valuation price/time.

## Inspection, changes and release

No Git or image changes can be applied through the frontend. A release must use a reviewed Git SHA/CI, schema migration and tested backup, an explicit owner approval, and a separate VPS deploy. Changing the max purchase budget does not reset wallet balances or change existing fixed stop/target prices.

Details: [DigitalOcean/SSH/Firewall](docs/security/DIGITALOCEAN_SSH_VPS_RUNBOOK.md), [Configuration and release/rollback](docs/security/CONFIG_RELEASE_ROLLBACK.md), [Encrypted backup/restore](docs/security/BACKUP_RESTORE.md), [Monitoring and incidents](docs/security/MONITORING_INCIDENT.md), [Security acceptance](docs/security/ACCEPTANCE_20261103.md), and [Final acceptance addendum](docs/security/FINAL_ACCEPTANCE_ADDENDUM_20261103.md).

**Not yet verified**: any DigitalOcean deployment or firewall; SSH tunnel across a VPS; automated backup retention/offsite copy; full encrypted-archive restore using an offline private key; real Coincheck order execution (intentionally forbidden). No profit prediction is warranted.
