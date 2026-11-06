"""FAST_START_PROXY: isolated, paper-only BTC/JPY experiment.

Signals always use BITBANK_PROXY hourly candles.  Simulated fills always use
COINCHECK_PUBLIC_BOOK.  This is deliberately not a venue-matched validation
and contains no authenticated endpoints, credentials, database access, or
order submission code.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import ssl
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .config import DEFAULT_CASH_JPY, MAX_BUY_JPY
from .paper import bracket, buy_from_asks, sell_to_bids
from .strategy import Candle, Signal, evaluate

UTC = timezone.utc
SIGNAL_SOURCE = "BITBANK_PROXY"
EXECUTION_SOURCE = "COINCHECK_PUBLIC_BOOK"
FEE_BPS = Decimal("0")  # simulation assumption only; not an exchange fee claim
MAX_BYTES = 2_000_000
FRESH_SECONDS = 90
CLOSE_DELAY = timedelta(minutes=2)


class FastStartError(RuntimeError): pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


def _now(now: datetime | None = None) -> datetime:
    result = now or datetime.now(UTC)
    if result.tzinfo is None: raise FastStartError("CLOCK_INVALID")
    return result.astimezone(UTC)


def _atomic(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(name): os.unlink(name)


def _json_atomic(path: Path, value: Any) -> None:
    _atomic(path, json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n")


def _decimal(value: Any) -> Decimal:
    try: out = Decimal(str(value))
    except (InvalidOperation, ValueError): raise FastStartError("MARKET_MALFORMED") from None
    if not out.is_finite() or out <= 0: raise FastStartError("MARKET_MALFORMED")
    return out


def _parse_rows(raw: bytes, source: str) -> tuple[list[Candle], list[str]]:
    try:
        source_data=json.loads(raw)
        if source_data.get("success") != 1 or source_data["data"]["candlestick"][0]["type"] != "1hour":
            raise FastStartError("DATA_SOURCE_INVALID")
        rows=source_data["data"]["candlestick"][0]["ohlcv"]
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise FastStartError("DATA_MALFORMED") from None
    candles: list[Candle] = []; risks: list[str] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 6: raise FastStartError("DATA_MALFORMED")
        try:
            o, h, l, c, v = map(float, row[:5]); stamp = int(row[5])
        except (TypeError, ValueError): raise FastStartError("DATA_MALFORMED") from None
        if not all(x == x and abs(x) != float("inf") for x in (o,h,l,c,v)) or min(o,h,l,c,v) < 0 or h < max(o,c) or l > min(o,c):
            raise FastStartError("DATA_MALFORMED")
        start = datetime.fromtimestamp(stamp / 1000, UTC)
        if stamp % 3_600_000 or o <= 0 or h <= 0 or l <= 0 or c <= 0: raise FastStartError("DATA_MALFORMED")
        if v == 0:
            if not o == h == l == c:
                raise FastStartError("ZERO_VOLUME_NONFLAT")
            risks.append("ZERO_VOLUME_PUBLISHED:" + start.isoformat())
        if len(source)==8 and source.isdigit() and start.strftime("%Y%m%d") != source:
            raise FastStartError("DATA_DAY_MISMATCH")
        candles.append(Candle(start, o, h, l, c, v))
    return candles, risks


def load_bootstrap(data_dir: Path, now: datetime | None = None) -> tuple[list[Candle], list[str]]:
    """Verify every manifest file before accepting the immutable bootstrap."""
    try: manifest = json.loads((data_dir / "manifest.json").read_text())
    except (OSError, ValueError): raise FastStartError("BOOTSTRAP_MANIFEST_INVALID") from None
    try: entries = manifest["entries"]
    except (OSError, ValueError, KeyError, TypeError): raise FastStartError("BOOTSTRAP_MANIFEST_INVALID") from None
    if manifest.get("source") != "public.bitbank.cc" or manifest.get("pair") != "btc_jpy" or manifest.get("tf") != "1hour":
        raise FastStartError("BOOTSTRAP_MANIFEST_INVALID")
    all_bars: list[Candle] = []; risks: list[str] = []
    for entry in entries:
        date, expected = entry.get("date"), entry.get("sha256")
        if not isinstance(date, str) or not isinstance(expected, str) or len(expected) != 64: raise FastStartError("BOOTSTRAP_MANIFEST_INVALID")
        try: raw = (data_dir / (date + ".json")).read_bytes()
        except OSError: raise FastStartError("BOOTSTRAP_FILE_MISSING") from None
        if hashlib.sha256(raw).hexdigest() != expected: raise FastStartError("BOOTSTRAP_SHA_MISMATCH")
        bars, flags = _parse_rows(raw, date)
        if len(bars) != entry.get("rows") or any(b.start.strftime("%Y%m%d") != date for b in bars): raise FastStartError("BOOTSTRAP_ROWS_INVALID")
        all_bars.extend(bars); risks.extend(flags)
    return _validate_hourly(all_bars), risks


def _validate_hourly(bars: list[Candle]) -> list[Candle]:
    bars = sorted(bars, key=lambda x: x.start)
    if not bars or len({b.start for b in bars}) != len(bars): raise FastStartError("DATA_DUPLICATE_OR_EMPTY")
    if any(bars[i].start - bars[i-1].start != timedelta(hours=1) for i in range(1, len(bars))):
        raise FastStartError("DATA_GAP")
    return bars


def aggregate(bars: list[Candle], hours: int, now: datetime) -> list[Candle]:
    """Only whole, closed UTC groups; no fabricated missing bars."""
    eligible = [b for b in bars if b.start + timedelta(hours=1) <= now - CLOSE_DELAY]
    groups: dict[datetime, list[Candle]] = {}
    for bar in eligible:
        start = bar.start.replace(hour=(bar.start.hour // hours) * hours, minute=0, second=0, microsecond=0)
        groups.setdefault(start, []).append(bar)
    result=[]
    for start, group in sorted(groups.items()):
        group=sorted(group, key=lambda b:b.start)
        if len(group) != hours or any(group[i].start != start + timedelta(hours=i) for i in range(hours)): continue
        if start + timedelta(hours=hours) <= now - CLOSE_DELAY:
            result.append(Candle(start, group[0].open, max(x.high for x in group), min(x.low for x in group), group[-1].close, sum(x.volume for x in group)))
    return result


def _http_json(url: str) -> Any:
    # URLs are selected only below; never accept operator-provided URL strings.
    opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(Request(url, headers={"User-Agent":"faststart-paper/1"}), timeout=8) as response:
            if response.status != 200 or response.geturl() != url: raise FastStartError("HTTP_STATUS")
            raw=response.read(MAX_BYTES + 1)
    except (URLError, OSError): raise FastStartError("HTTP_UNAVAILABLE") from None
    if len(raw) > MAX_BYTES: raise FastStartError("HTTP_TOO_LARGE")
    try: return json.loads(raw)
    except ValueError: raise FastStartError("HTTP_JSON_INVALID") from None


def _cache_dynamic(state_dir: Path, now: datetime, fetch: Callable[[str], Any]) -> list[Candle]:
    cache = state_dir / "cache"; outcome=[]
    for offset in (1, 0):
        date=(now.date()-timedelta(days=offset)).strftime("%Y%m%d")
        url=f"https://public.bitbank.cc/btc_jpy/candlestick/1hour/{date}"
        payload=fetch(url); raw=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        # Two-file checkpoint uses fail-closed checksum validation after writes.
        _atomic(cache / (date + ".json"), raw)
        _atomic(cache / (date + ".sha256"), hashlib.sha256(raw).hexdigest().encode())
    for file in sorted(cache.glob("20??????.json")):
        checksum=cache/(file.stem+".sha256")
        if not checksum.is_file() or checksum.read_text().strip()!=hashlib.sha256(file.read_bytes()).hexdigest():
            raise FastStartError("DYNAMIC_CACHE_SHA_MISMATCH")
        bars, _ = _parse_rows(file.read_bytes(), file.stem)
        outcome.extend(bars)
    return outcome


def _book(payload: Any) -> tuple[list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]:
    try: asks=[(_decimal(p),_decimal(q)) for p,q in payload["asks"]]; bids=[(_decimal(p),_decimal(q)) for p,q in payload["bids"]]
    except (KeyError, TypeError, ValueError): raise FastStartError("BOOK_MALFORMED") from None
    if not asks or not bids: raise FastStartError("BOOK_EMPTY")
    return asks, bids


def _quotes(fetch: Callable[[str], Any], now: datetime) -> tuple[Decimal, Decimal, list[tuple[Decimal,Decimal]], list[tuple[Decimal,Decimal]]]:
    bb=fetch("https://public.bitbank.cc/btc_jpy/ticker")
    cc=fetch("https://coincheck.com/api/ticker?pair=btc_jpy")
    book=fetch("https://coincheck.com/api/order_books?pair=btc_jpy")
    try:
        bitbank=_decimal(bb["data"]["last"]); coincheck=_decimal(cc["last"])
        bb_at=datetime.fromtimestamp(int(bb["data"]["timestamp"])/1000, UTC)
        cc_at=datetime.fromtimestamp(int(cc["timestamp"]),UTC)
    except (KeyError, TypeError, ValueError, OverflowError, OSError, AttributeError):
        raise FastStartError("TICKER_MALFORMED") from None
    for t in (bb_at,cc_at):
        age=(now-t).total_seconds()
        if not 0<=age<=FRESH_SECONDS: raise FastStartError("QUOTE_STALE_OR_CLOCK_SKEW")
    asks,bids=_book(book)
    if min(p for p,_ in asks)<max(p for p,_ in bids):
        raise FastStartError("BOOK_CROSSED")
    return bitbank, coincheck, asks, bids


def _initial_state() -> dict[str, Any]:
    return {"version":1,"mode":"PAPER_ONLY","signal_source":SIGNAL_SOURCE,"execution_source":EXECUTION_SOURCE,"fee_bps":"0","cash_jpy":str(DEFAULT_CASH_JPY),"position_btc":"0","entry":None,"stop":None,"take_profit":None,"realized_jpy":"0","last_bar":None,"last_filled_bar":None,"last_run_at":None,"last_quote_at":None,"last_fetched":{},"last_error":None,"next_event_id":1,"events":[]}


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists(): return _initial_state()
    try: value=json.loads(path.read_text())
    except (OSError, ValueError): raise FastStartError("STATE_INVALID") from None
    if value.get("mode") != "PAPER_ONLY" or value.get("signal_source") != SIGNAL_SOURCE or value.get("execution_source") != EXECUTION_SOURCE:
        raise FastStartError("STATE_SOURCE_INVALID")
    if value.get("version") != 1 or type(value.get("next_event_id")) is not int:
        raise FastStartError("STATE_VERSION_INVALID")
    events=value.setdefault("events",[])
    if not isinstance(events,list) or any(not isinstance(e,dict) for e in events):
        raise FastStartError("STATE_EVENTS_INVALID")
    return value


def _event(state: dict[str,Any], journal: Path, kind: str, detail: dict[str,Any], now: datetime) -> None:
    event={"id":state["next_event_id"],"at":now.isoformat(),"kind":kind,"signal_source":SIGNAL_SOURCE,"execution_source":EXECUTION_SOURCE,**detail}
    state["next_event_id"] += 1
    # Commit the event and balance as a single atomic JSON state update.
    # The human-readable journal is reconstructed only after state commit.
    state.setdefault("events",[]).append(event)


def _commit_state(state_path: Path, journal: Path, state: dict[str,Any]) -> None:
    _json_atomic(state_path,state)
    lines=[json.dumps(e,sort_keys=True,separators=(",",":")) for e in state.get("events",[])]
    _atomic(journal, ("\n".join(lines)+("\n" if lines else "")).encode())


def run_once(data_dir: Path, state_dir: Path, *, now: datetime | None = None, fetch: Callable[[str],Any] | None = None, refresh: bool = False, signal_fn: Callable[[list[Candle],list[Candle]],Signal] = evaluate) -> dict[str,Any]:
    """Run one crash-safe evaluation. A supplied fetch is solely for unit tests."""
    now=_now(now); state_dir.mkdir(mode=0o700, parents=True, exist_ok=True); os.chmod(state_dir,0o700)
    lock_path=state_dir/"faststart.lock"
    with lock_path.open("a+") as lock:
        os.chmod(lock_path,0o600); fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_path=state_dir/"state.json"; state=_read_state(state_path); journal=state_dir/"events.jsonl"
        state["last_error"] = None
        state["last_run_at"] = now.isoformat()
        try:
            bars, risks=load_bootstrap(data_dir, now)
            if bars[-1].start > now + timedelta(hours=2):
                raise FastStartError("CLOCK_SKEW_OR_FUTURE_DATA")
            if refresh:
                if fetch is None: fetch=_http_json
                dynamic=_cache_dynamic(state_dir, now, fetch)
                state["last_fetched"]={"bitbank_hourly_at":now.isoformat()}
                # The last bootstrapped hour was open when downloaded. Replace
                # only that hour and later hours with the independently fetched
                # (and hash-checked) dynamic market data.
                metadata=json.loads((data_dir/"manifest.json").read_text())
                cutoff_ms=metadata.get("closed_cutoff_ms")
                if not isinstance(cutoff_ms,int):
                    raise FastStartError("BOOTSTRAP_CUTOFF_MISSING")
                cutoff=datetime.fromtimestamp(cutoff_ms/1000,UTC)
                by_time={b.start:b for b in bars}
                for row in dynamic:
                    if row.start<cutoff and row.start in by_time and by_time[row.start]!=row:
                        raise FastStartError("HISTORICAL_SOURCE_CHANGED")
                    if row.start>=cutoff:
                        by_time[row.start]=row
                bars=_validate_hourly(list(by_time.values()))
            local, higher=aggregate(bars,2,now),aggregate(bars,4,now)
            signal=signal_fn(local,higher); bar=signal.snapshot.get("bar_start_utc") if signal.snapshot else None
            state["last_bar"]=bar
            state["zero_volume_risks"]=risks
            state["history"]={"confirmed_2h":len(local),"confirmed_4h":len(higher),
                              "required_2h":200,"required_4h":300,
                              "ready":len(local)>=200 and len(higher)>=300}
            state["signal"]={"valid":bool(signal.valid),"reason":signal.reason,
                             "indicator_snapshot":signal.snapshot}
            # Offline startup is a read-only signal evaluator, not a trading runtime.
            if fetch is None:
                state["last_error"]="OFFLINE_QUOTES_UNAVAILABLE"; _commit_state(state_path,journal,state); return state
            bitbank, coincheck, asks, bids=_quotes(fetch,now); state["last_quote_at"]=now.isoformat(); state.setdefault("last_fetched",{})["public_quotes_at"]=now.isoformat()
            state["cross_venue_basis_pct"]=str(((coincheck-bitbank)/bitbank*Decimal("100")).quantize(Decimal("0.0001")))
            if state["position_btc"] != "0":
                qty=Decimal(state["position_btc"]); stop=Decimal(state["stop"]); target=Decimal(state["take_profit"])
                best_bid=max(price for price, _ in bids)
                if best_bid <= stop or best_bid >= target:
                    fill=sell_to_bids(bids,qty,FEE_BPS)
                    if fill is None: raise FastStartError("EXIT_DEPTH_INSUFFICIENT")
                    state["cash_jpy"]=str(Decimal(state["cash_jpy"])+fill.net_sell_jpy); state["realized_jpy"]=str(Decimal(state["realized_jpy"])+fill.net_sell_jpy-Decimal(state["entry"])*qty)
                    state.update(position_btc="0",entry=None,stop=None,take_profit=None)
                    _event(state,journal,"PAPER_EXIT",{"reason":"stop" if best_bid <= stop else "take_profit","price":str(fill.price),"qty":str(fill.qty)},now)
            elif signal.valid and bar and state.get("last_filled_bar") != bar:
                fresh_bar=datetime.fromisoformat(bar).astimezone(UTC)
                basis=abs(bitbank-coincheck)/bitbank
                signal_closed=fresh_bar + timedelta(hours=2)
                if signal_closed > now:
                    state["last_error"]="ENTRY_UNCLOSED_SIGNAL"
                elif now-signal_closed <= timedelta(minutes=10) and basis <= Decimal("0.01"):
                    budget=min(MAX_BUY_JPY,Decimal(state["cash_jpy"])); fill=buy_from_asks(asks,budget,FEE_BPS)
                    if fill:
                        stop,target=bracket(fill.price,Decimal(str(signal.atr)))
                        state.update(cash_jpy=str(Decimal(state["cash_jpy"])-fill.total_buy_jpy),position_btc=str(fill.qty),entry=str(fill.price),stop=str(stop),take_profit=str(target),last_filled_bar=bar)
                        _event(state,journal,"PAPER_BUY",{"bar":bar,"price":str(fill.price),"qty":str(fill.qty),"jpy":str(fill.total_buy_jpy)},now)
                elif state["last_error"] is None:
                    state["last_error"]="ENTRY_STALE_SIGNAL" if now-signal_closed > timedelta(minutes=10) else "ENTRY_BASIS_OVER_1PCT"
            state["last_error"]=state.get("last_error") if state.get("last_error") else None
        except FastStartError as exc:
            state["last_error"]=str(exc)
        except (OSError, ValueError, TypeError):
            state["last_error"]="RUNTIME_INVALID"
        _commit_state(state_path,journal,state)
        return state


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description="PAPER_ONLY FAST_START_PROXY; never submits orders")
    parser.add_argument("--once",action="store_true",required=True)
    parser.add_argument("--data-dir",required=True,type=Path); parser.add_argument("--state-dir",required=True,type=Path)
    parser.add_argument("--refresh",action="store_true",help="fetch only fixed public HTTPS URLs")
    args=parser.parse_args(argv)
    state=run_once(args.data_dir,args.state_dir,refresh=args.refresh)
    print(json.dumps({k:state.get(k) for k in ("mode","signal_source","execution_source","last_bar","position_btc","last_error")},sort_keys=True))
    return 0 if not state.get("last_error") or state["last_error"] == "OFFLINE_QUOTES_UNAVAILABLE" else 2

if __name__ == "__main__": raise SystemExit(main())
