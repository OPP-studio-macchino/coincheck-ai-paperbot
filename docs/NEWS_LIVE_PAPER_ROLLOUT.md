# Verified live RSS news gate: paper-only rollout

This feature is deliberately pending operational activation. At the recorded status (2026-11-06 08:53 JST), the market history was only 18/200 confirmed 2-hour candles and 8/300 confirmed 4-hour candles. There were zero paper trades. The news gate must not be described as ready for paper buys until both warm-ups are genuinely complete; it does not change indicator periods or create candles.

## What live mode does

The default `compose.yaml` remains `NEWS_MODE=mock`. The configured default for real OpenAI assessment is `gpt-6-luna`, pending live validation and usage billing; strict JSON schema uses client-side length and count validation rather than unsupported schema keywords. The optional `compose.live-news.yaml` supplies the only live-mode opt-in. The news worker attempts three pinned HTTPS publisher RSS feeds; at least two independent publishers must provide fresh relevant records before CLEAR can be considered (one publisher outage is tolerated), with redirect refusal, 8-second request limits, 256 KiB bounds, public-host checks, XML DTD/entity rejection, fresh (<24h) entry dates, and no article fetches. It sends only RSS title, publisher short description, date, and canonical URL to OpenAI Responses. No web-search tool, secrets, portfolio data, or trading ability is supplied.

`clear` requires two cited, fresh, independently fetched publisher domains. `block` requires one cited verified receipt. Otherwise it is `unknown`. Stored bare URLs, stale receipts, and forged rows do not open the news gate. Baseline paper trading and ATR exit evaluation remain independent.

## Legitimate key setup and controlled paid opt-in

1. In the legitimate OpenAI Platform project selected by the operator, create a project API key with the minimum appropriate permission and billing controls. Do not paste it into shell history, `.env`, source, Docker build arguments, the API, or the frontend.
2. On the target host, create `./secrets/openai_api_key` with restrictive ownership/permissions using the established secret-management process. This repository does not contain that file. The news container alone mounts it as `/run/secrets/openai_api_key`.
3. First stage with mock mode and fixture/unit tests only. Inspect that `NEWS_MODE=mock` and no key is mounted into any service.
4. For a deliberately controlled paid paper test, use `docker compose -f compose.yaml -f compose.live-news.yaml config -q`, then the reviewed operator deployment process. This overlay sets `NEWS_MODE=live`, `ALLOW_BILLABLE_NEWS=I_ACCEPT_BILLING`, a 3600-second interval, and the worker enforces a durable pre-call reservation cap of 24 calls per UTC day. Missing/invalid secrets fail closed.
5. Observe status until warm-up reports 200/200 and 300/300 contiguous confirmed candles and a fresh verified assessment; do not infer readiness from a green service or a `clear` row alone.

No deployment, restart, migration, public RSS request, OpenAI API call, or Coincheck order was performed for this change.

## Build, audit, rollback

This isolated worktree started from the reviewed SHA `a6a5f75b91b95fae0aa6c5fa25e348e0a2d5edbf`. These implementation changes are intentionally uncommitted and no image was built or deployed. After the change is reviewed and committed, build only from that exact resulting SHA:

```
APP_GIT_SHA=SHA docker compose -f compose.yaml build
docker compose -f compose.yaml -f compose.live-news.yaml config -q
```

Record the resulting immutable image digest alongside `SHA`. To roll back, redeploy the previously audited image digest and configuration with the base `compose.yaml` alone (therefore mock news); do not use volume deletion or schema rollback as a rollback mechanism.

## Audited historical warm-up plan (not executed)

`Store.closed_candles` deliberately session-clamps candles: after a WebSocket reconnect, historical ticks/candles outside the active session can be ignored. Do not fabricate candles or lower the 2h/4h requirements.

A separately reviewed public-data backfill job should: (1) use Coincheck's documented public-trades pagination only, with fixed HTTPS host, bounded pages/bytes/timeouts and rate limits; (2) persist raw trade IDs/timestamps idempotently through the same validation path; (3) paginate deterministically backward with checkpoints, audit counts and gaps; (4) reconcile UTC 2h/4h candle continuity and trade counts before setting any readiness evidence; and (5) retain the source/pagination audit trail. Its roughly 50-day historical-feed need and possible gaps/retention limits are risk factors: a complete backfill cannot be assumed. It must run in a separate approved maintenance window, never against the running co-tenant services, and is explicitly not part of this rollout.

## Offline verification recorded for this worktree

`python3 -m unittest discover -v`: **45 tests passed**. `python3 -m compileall -q app tests`, `docker-compose -f compose.yaml -f compose.live-news.yaml config -q`, `git diff --check`, and the repository secret-pattern scan passed. (The host has legacy `docker-compose`; `docker compose` itself is unavailable.) These checks made no RSS request or OpenAI call.
