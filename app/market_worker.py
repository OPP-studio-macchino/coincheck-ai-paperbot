"""Public websocket ingestion, 2h signal and independent paper stop checks."""
from __future__ import annotations
import asyncio,json,logging,time
from datetime import datetime,timezone,timedelta
from decimal import Decimal

from .config import Settings
from .market import PublicQuote,parse_coincheck_trades
from .store import Store,bar_start,utc_now
from .strategy import evaluate,Signal

logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
URL='wss://ws-api.coincheck.com/'


def news_state(db:Store,now:datetime)->tuple[str,str]:
    row=db.latest_news()
    if not row:return 'unknown','no_assessment'
    state,summary,expires,sources,at=row
    if expires<=now:return 'unknown','assessment_expired'
    return state,summary


def check_signal(db:Store,quotes:PublicQuote,now:datetime):
    local=db.closed_candles(120,now,limit=450)
    upper=db.closed_candles(240,now,limit=450)
    if not local:return
    safe_cutoff=bar_start(now-timedelta(seconds=120),120)
    # No deferred trading from old signals: latest local candle must be most recently closed.
    if local[-1].start+timedelta(hours=2)!=safe_cutoff:return
    bar=local[-1].start
    if all(db.latest_bar_decided(n,bar) for n in ('baseline','news_gate')):return
    sig=evaluate(local,upper)
    if sig.reason=='warmup_incomplete' or sig.reason=='history_gap':
        return  # Preserve the incomplete history, never synthesize candles.
    asks=None
    if sig.valid:
        try:asks,_=quotes.fetch()
        except Exception:logging.warning('Public order book unavailable; paper buy will be skipped')
    state,reason=news_state(db,now)
    for name in ('baseline','news_gate'):
        action=db.apply_signal(name,bar,sig,asks,state,reason)
        logging.info('PAPER decision portfolio=%s bar=%s action=%s reason=%s',
                     name,bar.isoformat(),action,sig.reason)


def check_exits(db:Store,quotes:PublicQuote,price:Decimal,now:datetime,last_quote_at:float)->float:
    # Throttle quote requests; public-only, no private Coincheck requests.
    if time.monotonic()-last_quote_at<10:return last_quote_at
    exists=db.cn.execute('''SELECT 1 FROM portfolios WHERE btc_qty>0 AND
      (stop_price>=%s OR target_price<=%s) LIMIT 1''',(price,price)).fetchone()
    if not exists:return last_quote_at
    try:
        _,bids=quotes.fetch()
        result=db.exit_on_tick(price,bids)
        for event in result:logging.info('PAPER EXIT %s',event)
    except Exception:
        logging.warning('Exit quote unavailable; paper position remains open')
    return time.monotonic()


async def main():
    Settings.load()  # fail closed even before opening a websocket
    from websockets.asyncio.client import connect
    backoff=2
    while True:
        db=None;session=None
        try:
            db=Store();quotes=PublicQuote()
            async with connect(URL,ping_interval=20,ping_timeout=15,max_size=2_000_000) as ws:
                await ws.send(json.dumps({'type':'subscribe','channel':'btc_jpy-trades'}))
                session=db.connected();backoff=2
                logging.info('MARKET_CONNECTED session=%s',session)
                last_eval=0;last_quote=0
                async for data in ws:
                    ticks=parse_coincheck_trades(data)
                    if not ticks:continue
                    inserted=db.ingest(session,ticks)
                    now=utc_now()
                    if inserted:
                        for tick in ticks:
                            last_quote=check_exits(db,quotes,tick.price,now,last_quote)
                    if time.monotonic()-last_eval>15:
                        check_signal(db,quotes,now);last_eval=time.monotonic()
        except Exception as exc:
            logging.error('MARKET_DISCONNECTED type=%s',type(exc).__name__)
        finally:
            if db is not None:
                if session:
                    try:db.disconnected(session,'connection_lost_or_stopped')
                    except Exception:pass
                try:db.close()
                except Exception:pass
        await asyncio.sleep(backoff)
        backoff=min(120,backoff*2)

if __name__=='__main__':asyncio.run(main())
