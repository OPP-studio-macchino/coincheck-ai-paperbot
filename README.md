# Coincheck AI Paper Bot

**O.P.P Studio / BTC/JPY spot / DRY_RUN ONLY / isolated AI research**
**No actual Coincheck account, no authenticated Coincheck endpoint, no live exchange orders.**

Current development and release information is recorded in [Integration status](docs/INTEGRATION_STATUS_20261106.md). Dated security and MVP acceptance reports remain historical evidence. Code integration does not automatically update a running Bot, database, VPS, or API mode.

## Architecture

| Docker service | Role | Secrets | Host port |
|---|---|---|---|
| `db` | PostgreSQL durable ticks, 2h/4h candles, paper balances, news, decisions, audits | schema admin only | None |
| `migration` | Explicit versioned, non-destructive schema/role changes; profile-only | schema admin + role password files | None |
| `market` | Coincheck **PUBLIC** BTC/JPY trades and public order-book, strategy and paper exits | market DB role only | None |
| `news` | News risk assessment, default mock/unknown | news DB role only | None |
| `api` | Authenticated GET and audited POST pause/resume | query/control DB and view/admin tokens | None |
| `frontend` | Static Nginx UI and API reverse proxy | No credentials | `127.0.0.1:8097` for VPS, separate port for isolated tests |

The DB-only `db_private` and API-side `front_edge` networks are `internal:true`; market and news attach to a separate outbound network; frontend has a separate non-internal Docker transport solely for **127.0.0.1** host port publication. Do not expose DB or internal API ports to the host. Cloud Firewall, host listening and external reachability must be checked independently for each deployment. A Docker outbound bridge is not a host/domain egress allowlist.

## Frozen strategy (exact specification)

- Decision on **confirmed 2-hour candles** only; EMA21 > EMA55, local close above EMA21, EMA55 rising versus three bars earlier.
- RSI9 crosses above **55**, Window3 = crossover bar plus next three confirmed 2h bars, remains at least 55; deduplicate each RSI event.
- Higher timeframe: **previous confirmed 4-hour close > EMA50 > EMA200**; no future OHLCV.
- ADX and extra oscillator OFF. ATR14 fixed on entry: stop loss **3× ATR**, take profit **12× ATR**.
- Long-only, single position per virtual portfolio; `baseline` and `news_gate` are independent, each seeded with **100,000 JPY** on a genuinely new database only.
- New paper BUY inclusive of fees is capped by actual `BUY_BUDGET_JPY` (max **50,000 JPY**, can lower to 13,000). No automatic top-up/reset of current balances. Existing bracket prices do not change on new config.
- Minimum 0.001 BTC and 500 JPY value. No reliable historical Coincheck 2h/4h bar repair; collector conservatively requires continuous 200 confirmed 2h bars and 300 confirmed 4h bars before buying. A websocket gap triggers fail-closed history checks.
- `news_gate` blocks on mock/unknown/expired/unverified news; `baseline` is independent. News cannot initiate trades or override ATR exits. A model URL alone is not article verification.
- Public price stale/disconnected -> no BUY/SELL simulator fills, no misleading current valuation.

## Isolated development and verification

Use a separate checkout/worktree, Compose project, database/volume and non-conflicting loopback port. Inspect the Docker context before running a DB or HTTP test. The original Bot and security worktrees may hold preserved changes or data; their names are not permission to reuse their volumes.

```bash
# In the isolated checkout; never source a running Bot's secret environment.
python3 -B -m unittest discover -s tests -q
python3 -B scripts/scan_secrets.py --history
# On macOS, use an actual temporary directory instead of the /var symlink:
TMPDIR=/private/tmp python3 -B -m unittest discover -s tests -q
```

DB/HTTP E2E and migration require a separately confirmed disposable test database. `scripts/bootstrap_env.py` creates test credentials and must only target that isolated copy; migration is an explicit profile-only job. Follow the [security acceptance](docs/security/ACCEPTANCE_20261103.md) and [release/migration runbook](docs/security/CONFIG_RELEASE_ROLLBACK.md) after checking the actual context and project. The current integration evidence is in [Phase 2 acceptance](docs/ai-self-improvement/ACCEPTANCE_PHASE2_20261106.md).

Never run `docker compose down -v`, reset balances, or point `app.security_e2e` / `app.fixture_trades` at a running database. An old CI success does not attest to a new working tree.

In an isolated test frontend, use its verified loopback URL (for example `http://127.0.0.1:18098`). Enter the **view token** for read-only access or the **admin token** only when you need to pause/resume new paper buys. The browser holds the token in volatile memory (not URL, not localStorage). The code offers no remote shell, arbitrary Python, arbitrary Git branch execution or Docker administration.

## macOS SSH management tunnel

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
- Backups use streamed `pg_dump -Fc` into age encryption and SHA256. Preserve consistent backups and verify restoration into a separate DB. The owner-selected external HDD has a shared failure domain when it also holds the decryption key; record that limitation without treating a new paid storage contract or hardware purchase as a code-merge prerequisite.

**Health 200 is not trading-ready.** The dashboard reports effective strategy/Git SHA/budget, real-assets-unconnected status, latest market time, confirmed contiguous bar counts and shortfall reason, exit-evaluation availability, and valuation price/time.

## Inspection, changes and release

No Git or image changes can be applied through the frontend. A VPS release must use a reviewed Git SHA/CI, a schema compatibility check and any necessary migration, a tested backup, and separate deployment authorization. Code-merge authorization does not activate deployment. Changing the max purchase budget does not reset wallet balances or change existing fixed stop/target prices.

Details: [DigitalOcean/SSH/Firewall](docs/security/DIGITALOCEAN_SSH_VPS_RUNBOOK.md), [Configuration and release/rollback](docs/security/CONFIG_RELEASE_ROLLBACK.md), [Encrypted backup/restore](docs/security/BACKUP_RESTORE.md), [Monitoring and incidents](docs/security/MONITORING_INCIDENT.md), [Security acceptance](docs/security/ACCEPTANCE_20261103.md), and [Final acceptance addendum](docs/security/FINAL_ACCEPTANCE_ADDENDUM_20261103.md).

**Verification is date- and release-specific.** The handoff reports a VPS dry-run deployment and earlier clone-restore tests. This integration alone does not re-attest to current container contents, firewall/SSH settings, scheduled backups, or restore performance. See [Integration status](docs/INTEGRATION_STATUS_20261106.md) for source and time of each observation. Real Coincheck orders remain forbidden; no profit prediction is warranted.


## AI自己改善エンジン（v0.2）

入口は[AI_SELF_IMPROVEMENT.md](AI_SELF_IMPROVEMENT.md)、権限と仕様の正本は
[AI自己改善エンジン正本](docs/ai-self-improvement/SOURCE_OF_TRUTH.md)です。
計画書・技術書・運用手順・検収書を同じディレクトリで管理します。

`ai_lab/`は制約付きの候補生成、評価、記録を行います。通常Botの戦略、資金、
固定SL/TP、PostgreSQL、管理画面の操作権限を変えません。必要なmigration診断修正は
独立したBot側の差分として扱い、AIから既存DBへ接続しません。

既定は通信なし・固定応答です。

```bash
python3 -m ai_lab demo --workspace runtime/ai_lab --run-id first-demo --max-trials 6
# Saved research result: runtime/ai_lab/first-demo.html
```

Phase 2は、`prepare-proposal`で要求書を作り、専用生成器で1候補を保存し、
`evaluate-artifact`で同じデータ・設定に照合して評価します。実AIによる多段の自動探索や、
Botへの自動採用は本版にありません。fixtureとliveは別台帳で、fixtureのusageと費用は模擬値です。

liveは明示モード、`AI_LAB_PHASE2_LIVE=true`、専用キーfile、初期化済み有限予算、
期限内料金版を必要とします。呼出側の見積額を信用せず、固定モデルの上限から送信前に
費用と回数を原子予約します。失敗・timeout・crashも予約を消費し、再試行しません。

評価器は`compose.ai-lab.yaml`、fixture生成器は`compose.ai-lab.phase2.yaml`、
live生成器は`compose.ai-lab.phase2.live.yaml`の独立構成です。いずれもstandaloneで、
通常Botや相互のComposeと重ねません。具体的なコマンドと権限・費用・再開の制約は
[運用手順](docs/ai-self-improvement/OPERATIONS.md)を参照してください。

コードmerge、実API成功、実請求照合、利益改善、稼働反映は個別に判定します。
今回実行した検証と未確認事項は[Phase 2検収書](docs/ai-self-improvement/ACCEPTANCE_PHASE2_20261106.md)へ記録します。
