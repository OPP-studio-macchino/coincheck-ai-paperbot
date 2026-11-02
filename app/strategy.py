"""2h EMA21/55 RSI9 55 cross + previous confirmed 4h EMA50/200.

No future observations. Buy candidate only when two-hour candle is CLOSED.
ATR14 is frozen at signal, 3x stop and 12x take profit.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from math import isfinite

UTC = timezone.utc


@dataclass(frozen=True)
class Candle:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Signal:
    valid: bool
    reason: str
    atr: float | None
    snapshot: dict


def ema(values: list[float], n: int) -> list[float]:
    if n <= 0: raise ValueError('n > 0')
    result=[]
    last=None
    k=2/(n+1)
    for value in values:
        if not isfinite(value): raise ValueError('non-finite input')
        last = value if last is None else k*value+(1-k)*last
        result.append(last)
    return result


def wilder(values: list[float], n: int) -> list[float | None]:
    if n <= 0: raise ValueError('n > 0')
    out=[]
    acc=[]
    prior=None
    for x in values:
        if not isfinite(x): raise ValueError('non-finite input')
        if prior is None:
            acc.append(x)
            if len(acc) == n: prior=sum(acc)/n
        else:
            prior=(prior*(n-1)+x)/n
        out.append(prior)
    return out


def rsi(close: list[float], n: int=9) -> list[float | None]:
    if len(close) < 2: return [None]*len(close)
    gains=[max(close[i]-close[i-1],0) for i in range(1,len(close))]
    losses=[max(close[i-1]-close[i],0) for i in range(1,len(close))]
    ups=wilder(gains,n)
    downs=wilder(losses,n)
    out=[None]
    for u,d in zip(ups,downs):
        if u is None or d is None:out.append(None)
        elif d==0 and u==0:out.append(50.)
        elif d==0:out.append(100.)
        else:out.append(100-100/(1+u/d))
    return out


def atr(candles: list[Candle], n:int=14) -> list[float | None]:
    if not candles:return []
    tr=[]
    for i,c in enumerate(candles):
        if c.high<max(c.open,c.close) or c.low>min(c.open,c.close):
            raise ValueError('Malformed OHLC')
        prev=candles[i-1].close if i else c.close
        tr.append(max(c.high-c.low,abs(c.high-prev),abs(c.low-prev)))
    return wilder(tr,n)


def contiguous(bars:list[Candle], seconds:int) -> bool:
    return all(b.start.tzinfo is not None for b in bars) and all(
        int((bars[i].start-bars[i-1].start).total_seconds())==seconds
        for i in range(1,len(bars)))


def evaluate(local:list[Candle], higher:list[Candle]) -> Signal:
    """Long candidate; accepts only complete candles from uninterrupted collector session."""
    if len(local)<200 or len(higher)<300:
        return Signal(False, 'warmup_incomplete', None, {'2h':len(local),'4h':len(higher)})
    if not contiguous(local,7200) or not contiguous(higher,14400):
        return Signal(False, 'history_gap', None, {})
    bar_end=local[-1].start+timedelta(hours=2)
    confirmed=[c for c in higher if c.start+timedelta(hours=4)<=bar_end]
    if len(confirmed)<300:
        return Signal(False, 'higher_timeframe_warmup', None, {'4h_closed':len(confirmed)})
    local_end=local[-1].start
    hclose=confirmed[-1]
    if hclose.start+timedelta(hours=4)>bar_end:
        return Signal(False, 'future_higher_timeframe', None, {})
    close=[c.close for c in local]
    fast=ema(close,21);slow=ema(close,55)
    rv=rsi(close,9)
    av=atr(local,14)
    upper=[c.close for c in confirmed]
    hfast=ema(upper,50);hslow=ema(upper,200)
    idx=len(local)-1
    latest_rsi=rv[idx]
    recent_cross=next((idx-j for j in range(4)
                      if idx-j>0 and rv[idx-j] is not None and rv[idx-j-1] is not None
                      and rv[idx-j-1]<=55<rv[idx-j]),None)
    trend=(close[idx]>fast[idx]>slow[idx]>slow[idx-3])
    higher_trend=(upper[-1]>hfast[-1]>hslow[-1])
    window_ok=recent_cross is not None and latest_rsi is not None and latest_rsi>=55
    cand=trend and higher_trend and window_ok and av[idx] is not None
    info={'bar_start_utc':local_end.isoformat(),'rsi9':round(latest_rsi,5) if latest_rsi is not None else None,
          'ema21':round(fast[-1],4),'ema55':round(slow[-1],4),
          'higher_ema50':round(hfast[-1],4),'higher_ema200':round(hslow[-1],4),
          'atr14':round(av[idx],4) if av[idx] is not None else None,
          'rsi_window_age':idx-recent_cross if recent_cross is not None else None,
          'trend_ok':trend,'higher_ok':higher_trend,'rsi_window_ok':window_ok}
    return Signal(bool(cand),'signal' if cand else 'conditions_not_met',av[idx],info)
