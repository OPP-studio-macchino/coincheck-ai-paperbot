# Acceptance checklist

- [ ] Local Colima / Docker Engine and Compose start (verified using ps and health checks)
- [ ] Postgres health succeeds, schemas and two paper portfolios present
- [ ] Market public websocket authentic source and tick persistence confirmed
- [ ] 2h and 4h candle derivation; partial and interrupted data rejected
- [ ] 2h indicators / RSI cross correctly match independent dataset (not just unit fixtures)
- [ ] ATR stop and take-profit remain active if news API fails
- [ ] Double purchase and unknown order states explicitly prevented
- [ ] GET status/decisions/fills/news/candles and authenticated POST paper pause work via frontend
- [ ] News mock has zero paid calls; live is owner activated and bounded
- [ ] Public GitHub contains zero private keys, DB, trade history, .env, internal paths
- [ ] VPS hostname/IP and deployment approved, isolated from existing apps
- [ ] 300 continuous fully closed 4h bars accumulated before signal/entry eligibility
- [ ] Real Coincheck buy/sell orders: prohibited, 0 submitted

Passing unit tests or starting containers is not proof of profitability, reliable fill simulation, or readiness for real orders.
