# Architecture and invariants

- Python is the sole strategy implementation. No TradingView dependency.
- Data ingress: `wss://ws-api.coincheck.com` public `btc_jpy-trades`, price/size and id. Duplicate IDs are discarded. Collector saves both OHLC aggregates and source ticks to PostgreSQL.
- Collector coverage: only candles from the latest uninterrupted websocket session count as valid. Startup partial bars are excluded; a disconnect invalidates the continuity window. No synthetic price gap fill, no backdated trading signal.
- Entry strategy: most recent fully closed 2h bar, EMA21/55, RSI9 upward cross 55 (0..3 previous bars), slow EMA rising over 3 bars, last confirmed 4h close above EMA50>EMA200, ATR14 positive. Warmup: 2h 200 candles, 4h 300 candles.
- Paper portfolios: baseline and news_gate; both with cash JPY 100,000 initial and 50,000 max purchase. Single long position, no pyramiding. New entries are recorded in a single DB transaction with `decisions(portfolio,bar_start)` UNIQUE; retry cannot double buy a bar.
- ATR bracket set at paper entry: entry ± ATR14*{3,12}. At every websocket tick, stop/target tested independently of news and new-entry loop. Current public book is used for simulated fill; lack of liquidity means unresolved paper position is retained and warning logged.
- News gate: `unknown` until validated official-source structured output arrives, expires <=60 min. Mock mode records unknown; no billable calls. News only affects **new entries**, never modifies stop or target.
- Security: API is internal to Compose, frontend binds 127.0.0.1, bearer required for GET and POST except /healthz, no COINCHECK private endpoint code. Generated .env ignored. The frontend token is memory-only. A future public HTTPS/TLS/auth layer is a separate deployment task.
- PostgreSQL schema is initialized on first creation through read-only mounted `schema.sql`. Existing volumes are not reset. Schema evolution needs migrations; do not run `down -v`.
