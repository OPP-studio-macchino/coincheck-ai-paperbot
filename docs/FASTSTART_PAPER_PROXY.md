# FAST_START_PROXY (paper-only experimental system)

This is a separate, self-contained BTC/JPY experiment. `signal_source` is always
`BITBANK_PROXY`: it evaluates the existing 2-hour/4-hour strategy using bitbank
hourly candles. `execution_source` is always `COINCHECK_PUBLIC_BOOK`: it models
fills from Coincheck's public asks and exits from public bids. It never uses an
authenticated endpoint, API key, database, existing container, or real order.

It is **not venue-matched strategy validation**, is not real-ready, and says
nothing guaranteed about profitability. Price, liquidity, candle construction,
and timing can diverge between bitbank and Coincheck. The fee assumption is
currently 0 bps for simulation and is disclosed in state; it is not a claim
about actual Coincheck fees.

## Data and safety

The immutable bootstrap directory contains bitbank daily 1-hour files and a
SHA-256 manifest. Every file is verified before use. Published flat
zero-volume candles are retained and risk-marked; missing hours are never
invented. 2h/4h bars are derived only from this same bitbank feed, with a
two-minute UTC closure delay. Future dynamic current/previous-day cache files,
when `--refresh` is explicitly used, live only under the private state directory
and are atomically written with hashes. Historical proxy candles must never be
mixed with Coincheck candles in an indicator series.

State uses an owner-private (0700) directory, a flock lock, atomic/fsynced
replace, and an append-style event journal with monotonic IDs. It has one long
position maximum, starts with 100,000 JPY paper cash, caps a buy at 50,000 JPY,
and freezes ATR14 ×3 stop / ×12 target at entry. Entry is blocked on a stale
(over 10 minutes) signal, malformed/missing data, a cross-venue basis over 1%,
or unavailable/depth-insufficient public quotes. Exits are checked independently
of new entries and are never news-gated. A false signal never becomes a forced
buy.

## Smoke and scheduling

Run the non-network smoke first (it evaluates only the verified staged history):

```sh
python3 -m app.faststart_paper --once --data-dir /path/to/bitbank-1h-bootstrap-20261106 --state-dir /owner/private/faststart-state
```

`--refresh` is the only mode that accesses network data, and uses only fixed
public bitbank candle/ticker and Coincheck ticker/order-book HTTPS URLs. Do not
enable it until accepted. A separately authorized, unprivileged `bot-admin`
cron entry may run the same command every five minutes; do not install it here:

```cron
*/5 * * * * /usr/bin/python3 -m app.faststart_paper --once --refresh --data-dir /path/to/bitbank-1h-bootstrap-20261106 --state-dir /owner/private/faststart-state >>/owner/private/faststart-state/cron.log 2>&1
```

Verify tomorrow by inspecting the safe `state.json` for `BITBANK_PROXY`,
`COINCHECK_PUBLIC_BOOK`, fresh timestamps, and classified errors. No trade is
expected or guaranteed if the strategy remains invalid. Roll back by removing
the authorized cron line and retaining the private state for audit; this never
touches current containers, Postgres, FX services, or their data. There is no
LIVE migration path in this experiment.

## Optional staged-data validation

This is intentionally opt-in because the staged input is outside the worktree:

```sh
python3 scripts/validate_faststart_staged.py /Volumes/OPP_ARCHIVE/重要データ/Coincheck-AI-Bot-Release-Staging/bitbank-1h-bootstrap-20261106
```
