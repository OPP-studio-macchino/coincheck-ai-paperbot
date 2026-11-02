# Coincheck AI Paper Bot — Docker / VPS-ready, DRY_RUN only

**O.P.P Studio | Research only | 2026-11**

A fully separate, paper-only BTC/JPY application with **Coincheck public websocket trades**, 2-hour strategy, OpenAI news risk gate, PostgreSQL, API, and a responsive dashboard. No authenticated Coincheck API calls, no real-order adapter and no real BTC/JPY funds. **TradingView is not used.**

## Runtime design

| Container | Role | Exposed publicly? |
|---|---|---|
| `db` | PostgreSQL: ticks, 2h/4h candles, paper ledger, news, audit | No |
| `market` | Public Coincheck BTC/JPY trades websocket, 2h signal and independent ATR paper exits | No |
| `news` | News assessment every 30 min: `mock` (default) / OpenAI `live` (opt-in paid API) | No |
| `api` | Authenticated GET/POST REST endpoints | No |
| `frontend` | Japanese management UI and same-origin API reverse proxy | **127.0.0.1:8097 only** |

The single Python strategy is **EMA21/55 + RSI9 cross above 55 with a three-bar window, slow EMA rising, previous completed 4h close > EMA50 > EMA200, ATR14 stop 3x / take 12x**. Only confirmed 2h candles can generate new buys. Paper budget **50,000 JPY** per buy, starting paper cash **100,000 JPY** in each of the **baseline** and **news_gate** portfolios. No averaging-in. Open position exit checks run independently of two-hour entry decisions and OpenAI calls.

The latest offline exploration used bitbank data, not Coincheck, and did not establish future profits. To avoid fabricating Coincheck history, startup does **not** backfill candles from other exchanges. The live Coincheck collector must accumulate **at least 300 complete uninterrupted 4h candles** (around 50 days) and 200 2h candles before buys can become eligible. Any websocket disconnect restarts this continuity window. Frontend shows incomplete warmup; never calls incomplete data a valid strategy signal. No stale-bar catch-up orders.

## Local Docker usage

Prerequisites: Docker Engine + Compose (macOS: Colima). Project source, DB backups and audit files live on an external disk. On a fresh checkout:

```bash
python3 scripts/bootstrap_env.py   # Generates .env permissions 0600; never commit it
# edit NEWS_MODE=mock (default), API key blank
# macOS Homebrew: docker-compose is installed separately
# e.g. colima start --profile coincheck-ai
# Make sure Docker/Compose daemon is reachable
docker compose config --quiet
docker compose up --build -d
# If `docker compose` plugin is unavailable, use `docker-compose`.
docker compose ps
```

Open http://127.0.0.1:8097 in the **same machine**, or access the VPS through an SSH tunnel (not a public port). Paste `DASHBOARD_TOKEN` locally into the frontend form; it stays only in browser memory. Never paste a token into chat or logs. You can inspect it locally with a text editor; do not copy to GitHub.

```bash
docker compose logs --tail=50 market news api
# A completely separate, read-only CLI health check (doesn't trade)
curl -f http://127.0.0.1:8097/healthz
# Backup PostgreSQL before upgrades
bash scripts/backup.sh
```

**Never use `docker compose down -v`**: that deletes the database volume. Do not publish raw Docker ports, the DB, API keys, database password, event logs or news history to GitHub.

## API frontend (token required)

- `GET /api/status`: market status, last tick, cash/BTC per paper portfolio, news status.
- `GET /api/candles?timeframe=120&limit=120`: collected 2-hour (or 4-hour with 240) OHLCV, UTC timestamps.
- `GET /api/decisions`, `GET /api/fills`, `GET /api/news`: audit history.
- `POST /api/control/pause` JSON `{"paused":true|false}`: pause or resume **new paper buys only**. No forced sale.
- `GET /healthz`: Docker health, no auth, no sensitive data.

Admin calls require `Authorization: Bearer <DASHBOARD_TOKEN>`. The frontend port is bound only to localhost; VPS access requires a tunnel or a separate authenticated TLS reverse proxy, **not supplied in this MVP**.

## News mode and API cost

Default `NEWS_MODE=mock` means **no OpenAI API calls and no news-based purchasing**. The baseline paper account can trade once market history is ready, while the news-gated account refuses new buys until a verifiable, unexpired `clear` assessment exists. `NEWS_MODE=live` plus a private `OPENAI_API_KEY` enables periodic Responses API search and structured classification (web_search can incur separate charges). News failure, missing official sources or out-of-date results yield `unknown` and block news-gated **new purchases**. Existing ATR paper exits are never blocked. The server caps live news assessment calls at 48 per UTC day. The model's text never executes code or orders.

## Deployment and limits

The Compose stack is **VPS-ready but not live-deployed**: choose a dedicated Linux VPS and run it with firewall restrictions, backups, HTTPS/auth or an SSH tunnel, log rotation, and monitoring. Avoid reusing unrelated production hosts without explicit targeting and inspection.

- **No real trades**: mandatory `DRY_RUN=true` and `LIVE_TRADING_ENABLED=false`; private Coincheck credentials rejected. Orders/fills are simulated using public order-book depth, not guaranteed execution.
- **No complete historical restore**: public websocket outages cannot reliably be backfilled through Coincheck's short REST history; continuity resets. Use independently verified native Coincheck historical data in a future scoped importer, never mix bitbank OHLCV into Coincheck execution.
- **Tick/order-book difference**: triggers are based on trade prints, simulated fill prices on current public orderbook. Fast-market gaps, partial book updates and latency can alter results.
- **No full production hardening yet**: no user management, public HTTPS, migrations, rate limit on admin endpoints, broker reconciliation, tax reporting, or actual fail-safe orders. No financial performance promise.
- **No API key in repo**. OpenAI key creation, configuring `NEWS_MODE=live` and incurring billable calls are separate Owner-approved setup tasks.

See `docs/ACCEPTANCE.md`, `docs/ARCHITECTURE.md`, `docs/SECURITY.md` for readiness gates.
