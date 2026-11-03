"""Isolated websocket collection, 2h entry evaluation and independent ATR exit loop.

Only PUBLIC Coincheck trades/orderbook; no private API adapter. No news calls here.
"""
from __future__ import annotations
import asyncio,json,logging,time
from datetime import datetime,timezone,timedelta
from decimal import Decimal

from .config import Settings
from .market import PublicQuote,parse_coincheck_trades
from .store import Store,bar_start,utc_now
from .strategy import evaluate

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
URL="wss://ws-api.coincheck.com/"

def current_news(db:Store,now:datetime,mode:str="mock")->tuple[str,str]:
    # Changing a database row to clear can never override mock mode.
    if mode!="live":
        return "unknown","mock_mode_forbids_news_clear"
    row=db.latest_news()
    if not row:return "unknown","no_assessment"
    state,summary,expires,sources,at=row
    if not expires or expires<=now:return "unknown","assessment_expired"
    # This security release contains no independent publisher fetcher.
    # A model's JSON or forged row is insufficient to authorize news_gate.
    if state=="clear":return "unknown","independent_evidence_not_verified"
    return ("block","headline_risk") if state=="block" else ("unknown","unverified_news")

def check_signal(db:Store,quotes:PublicQuote,s:Settings,now:datetime):
    history=db.history_status(now)
    if not history["history_ready"]:return "not_ready:"+str(history["reason"])
    local=db.closed_candles(120,now,limit=450)
    upper=db.closed_candles(240,now,limit=450)
    if not local or not upper:return "no_closed_history"
    bar=local[-1].start
    end=bar+timedelta(minutes=120)
    if end!=bar_start(now-timedelta(seconds=120),120):return "not_latest_closed_bar"
    if (now-end).total_seconds()>600:return "signal_too_old"
    if all(db.latest_bar_decided(n,bar) for n in ("baseline","news_gate")):
        return "already_decided"
    sig=evaluate(local,upper)
    asks=None
    if sig.valid:
        try:
            t0=time.monotonic()
            asks,_=quotes.fetch()
            if time.monotonic()-t0>8:asks=None
        except Exception:
            logging.warning("BUY_QUOTE_UNAVAILABLE")
    state,reason=current_news(db,now,s.news_mode)
    for name in ("baseline","news_gate"):
        action=db.apply_signal(name,bar,sig,asks,state,reason,s,evaluated_at=utc_now())
        logging.info("PAPER_DECISION portfolio=%s action=%s signal=%s",
                     name,action,sig.reason)
    db.set_runtime(s,last_eval_at=now)
    return "evaluated"

async def signal_loop(s:Settings):
    db=Store("market")
    quotes=PublicQuote()
    try:
        while True:
            await asyncio.sleep(15)
            try:
                # Quote network I/O cannot block the websocket event loop.
                result=await asyncio.to_thread(check_signal,db,quotes,s,utc_now())
                if result not in ("already_decided","evaluated"):
                    logging.debug("SIGNAL_GATE %s",result)
            except Exception as e:
                logging.error("SIGNAL_UNAVAILABLE type=%s",type(e).__name__)
                # History remains intact; never trade on stale signals.
    finally:db.close()

async def exit_loop(s:Settings,queue:asyncio.Queue):
    db=Store("market")
    quotes=PublicQuote()
    try:
        while True:
            tick=await queue.get()
            try:
                now=utc_now()
                if tick.at>now+timedelta(seconds=5) or now-tick.at>timedelta(seconds=30):
                    db.set_runtime(s,exit_at=now,exit_reason="stale_market_tick")
                    continue
                holdings=db.cn.execute("SELECT 1 FROM portfolios WHERE btc_qty>0 LIMIT 1").fetchone()
                if not holdings:
                    db.set_runtime(s,market_at=tick.at,exit_at=now,exit_reason="no_position")
                    continue
                triggered=db.cn.execute("""SELECT 1 FROM portfolios WHERE btc_qty>0
                    AND (stop_price>=%s OR target_price<=%s) LIMIT 1""",
                    (tick.price,tick.price)).fetchone()
                if not triggered:
                    db.set_runtime(s,market_at=tick.at,exit_at=now,exit_reason="no_exit_trigger")
                    continue
                try:
                    start=time.monotonic()
                    _,bids=await asyncio.to_thread(quotes.fetch)
                    if time.monotonic()-start>8:
                        db.set_runtime(s,exit_at=now,exit_reason="public_quote_timeout")
                        continue
                    actions=db.exit_on_tick(tick.price,bids,s.fee_bps)
                    db.set_runtime(s,market_at=tick.at,exit_at=utc_now(),
                                   exit_reason="exit_processed" if actions else "no_fill_liquidity")
                    for event in actions:logging.info("PAPER_EXIT %s",event)
                except Exception as e:
                    db.set_runtime(s,exit_at=now,exit_reason="public_quote_unavailable")
                    logging.warning("PAPER_EXIT_UNAVAILABLE type=%s",type(e).__name__)
            except Exception as e:
                logging.error("EXIT_EVALUATION_FAILED type=%s",type(e).__name__)
            finally:queue.task_done()
    finally:db.close()

async def main():
    s=Settings.load()
    from websockets.asyncio.client import connect
    backoff=2
    while True:
        db=None;session=None;tasks=[]
        try:
            db=Store("market")
            db.set_runtime(s,exit_reason="collector_startup")
            quotes=PublicQuote()
            async with connect(URL,ping_interval=20,ping_timeout=15,max_size=2_000_000) as ws:
                await ws.send(json.dumps({"type":"subscribe","channel":"btc_jpy-trades"}))
                session=db.connected()
                backoff=2
                queue=asyncio.Queue(maxsize=2000)
                tasks=[asyncio.create_task(signal_loop(s)),asyncio.create_task(exit_loop(s,queue))]
                logging.info("MARKET_CONNECTED session=%s",session)
                last_runtime=0.0
                async for data in ws:
                    ticks=parse_coincheck_trades(data)
                    if not ticks:continue
                    inserted=db.ingest(session,ticks)
                    now=utc_now()
                    if inserted:
                        for t in inserted:
                            # Only newly persisted, fresh trade IDs can drive exits.
                            if not timedelta(0)<=now-t.at<=timedelta(seconds=30):continue
                            if queue.full():
                                db.set_runtime(s,exit_at=now,exit_reason="exit_queue_overflow")
                                raise RuntimeError("Exit queue overflow: reconnect required")
                            queue.put_nowait(t)
                    if inserted and time.monotonic()-last_runtime>=20:
                        db.set_runtime(s,market_at=max(t.at for t in inserted))
                        last_runtime=time.monotonic()
        except Exception as e:
            logging.error("MARKET_DISCONNECTED category=%s",type(e).__name__)
        finally:
            for task in tasks:task.cancel()
            if tasks:await asyncio.gather(*tasks,return_exceptions=True)
            if db:
                if session:
                    try:db.disconnected(session,"network_lost_or_stopped")
                    except Exception:pass
                try:db.set_runtime(s,exit_reason="market_session_disconnected")
                except Exception:pass
                try:db.close()
                except Exception:pass
        await asyncio.sleep(backoff)
        backoff=min(120,backoff*2)

if __name__=="__main__":asyncio.run(main())
