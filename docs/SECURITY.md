# Security boundary

PUBLIC GITHUB REPOSITORY. This repository must never contain `.env`, OpenAI keys, Coincheck credentials, private holdings, downloaded account exports, raw trade history, database files, JWTs or host SSH config.

No live order endpoint exists. In `config.py`, `DRY_RUN=false`, `LIVE_TRADING_ENABLED=true` and the presence of Coincheck private API credentials cause startup rejection. Do not silently add an execution adapter. For production trading a new security assessment, order reconciliation, user approval and independent acceptance are mandatory.

The management API supports **paper buy pause/resume only**, not live buy/sell. Its bearer token is generated and held out of git, and the frontend network port binds loopback only. For a VPS, use an SSH tunnel or future authenticated TLS deployment; never simply replace 127.0.0.1 with 0.0.0.0.

OpenAI Responses API searches can be billable; defaults to mock, no key, zero calls. Classified text and external webpages are untrusted input. Model-generated strings cannot control SQL, shell, Docker, trade calls or risk calculations. Responses undergo local validation and fail to `unknown` when stale or unsupported.
