"""PostgreSQL ledger with atomic paper fills and per-bar decision idempotency."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import uuid

from .config import Settings
from .strategy import Candle, Signal
from .market import TradeTick
from .paper import buy_from_asks, sell_to_bids, bracket, MAX_BUY

UTC=timezone.utc


def utc_now():return datetime.now(UTC)


def bar_start(at:datetime, minutes:int)->datetime:
    seconds=int(at.timestamp());size=minutes*60
    return datetime.fromtimestamp((seconds//size)*size,UTC)


def ceil_bar(at:datetime,minutes:int)->datetime:
    b=bar_start(at,minutes)
    return b if b>=at else b+timedelta(minutes=minutes)


class Store:
    def __init__(self):
        import psycopg
        self.cn=psycopg.connect(Settings.load().database_url,autocommit=True)

    def close(self):self.cn.close()

    def connected(self):
        session=str(uuid.uuid4())
        with self.cn.transaction():
            self.cn.execute('INSERT INTO feed_sessions(id,connected_at) VALUES(%s,%s)',(session,utc_now()))
        return session

    def disconnected(self,session:str,reason:str='disconnected'):
        self.cn.execute('UPDATE feed_sessions SET disconnected_at=now(),error_text=%s WHERE id=%s',
                        (reason[:300],session))

    def ingest(self,session:str,ticks:list[TradeTick])->int:
        inserted=0
        with self.cn.transaction():
            for tick in ticks:
                answer=self.cn.execute('''INSERT INTO market_ticks(trade_id,trade_at,price,amount)
                    VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING trade_id''',
                    (tick.trade_id,tick.at,tick.price,tick.quantity)).fetchone()
                if not answer:continue
                inserted+=1
                for minutes in (120,240):
                    bucket=bar_start(tick.at,minutes)
                    self.cn.execute('''INSERT INTO candles(timeframe_min,bar_start,open,high,low,close,
                        volume,trade_count,first_at,last_at)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,1,%s,%s)
                        ON CONFLICT(timeframe_min,bar_start) DO UPDATE SET
                        open=CASE WHEN EXCLUDED.first_at<candles.first_at THEN EXCLUDED.open ELSE candles.open END,
                        close=CASE WHEN EXCLUDED.last_at>=candles.last_at THEN EXCLUDED.close ELSE candles.close END,
                        high=GREATEST(candles.high,EXCLUDED.high),
                        low=LEAST(candles.low,EXCLUDED.low),
                        volume=candles.volume+EXCLUDED.volume,
                        trade_count=candles.trade_count+1,
                        first_at=LEAST(candles.first_at,EXCLUDED.first_at),
                        last_at=GREATEST(candles.last_at,EXCLUDED.last_at)''',
                        (minutes,bucket,tick.price,tick.price,tick.price,tick.price,
                         tick.quantity,tick.at,tick.at))
            if ticks:
                self.cn.execute('UPDATE feed_sessions SET last_trade_at=%s WHERE id=%s',
                                (max(t.at for t in ticks),session))
        return inserted

    def current_session(self):
        return self.cn.execute('''SELECT connected_at,last_trade_at FROM feed_sessions
            WHERE disconnected_at IS NULL ORDER BY connected_at DESC LIMIT 1''').fetchone()

    def closed_candles(self,minutes:int,now:datetime,limit:int=400)->list[Candle]:
        # Strictly exclude the partial first candle after reconnect, plus incomplete newest bar.
        session=self.current_session()
        if not session or not session[1]:return []
        connected, last_trade=session
        if (now-last_trade).total_seconds()>120:return []
        safe_start=ceil_bar(connected,minutes)
        safe_end=bar_start(now-timedelta(seconds=120),minutes)
        rows=self.cn.execute('''SELECT bar_start,open,high,low,close,volume
          FROM candles WHERE timeframe_min=%s AND bar_start>=%s AND bar_start+make_interval(mins=>%s)<=%s
          ORDER BY bar_start DESC LIMIT %s''',(minutes,safe_start,minutes,safe_end,limit)).fetchall()
        candles=[Candle(r[0],*(float(x) for x in r[1:])) for r in reversed(rows)]
        return candles

    def latest_news(self):
        return self.cn.execute('''SELECT state,summary,expires_at,sources,at FROM news_assessments
            ORDER BY at DESC LIMIT 1''').fetchone()

    def record_news(self,state:str,summary:str,sources:list,expires_at:datetime,model:str,usage:dict):
        from psycopg.types.json import Jsonb
        if state not in ('clear','block','unknown'):raise ValueError('bad risk state')
        self.cn.execute('''INSERT INTO news_assessments(state,summary,sources,expires_at,model,usage)
          VALUES(%s,%s,%s,%s,%s,%s)''',
          (state,summary[:1500],Jsonb(sources),expires_at,model,Jsonb(usage)))

    def daily_news_calls(self):
        return self.cn.execute('''SELECT COUNT(*) FROM news_assessments
           WHERE at>=date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
             AND model NOT IN ('mock','api_failed','budget_exceeded')''').fetchone()[0]

    def log_error(self,subsystem:str,message:str):
        # Never log API keys, URLs with tokens, exception values containing secrets.
        self.cn.execute('INSERT INTO errors(subsystem,message) VALUES(%s,%s)',(subsystem[:40],message[:220]))

    def paused(self):
        return self.cn.execute('SELECT paused FROM control_state WHERE id=1').fetchone()[0]

    def set_paused(self,paused:bool):
        self.cn.execute('UPDATE control_state SET paused=%s,updated_at=now() WHERE id=1',(paused,))

    def latest_bar_decided(self,portfolio:str,bar:datetime)->bool:
        return bool(self.cn.execute('SELECT 1 FROM decisions WHERE portfolio=%s AND bar_start=%s',
                                    (portfolio,bar)).fetchone())

    def apply_signal(self,portfolio:str,bar:datetime,signal:Signal,
                     asks:list|None,news_state:str,news_reason:str)->str:
        """A per-bar idempotent transaction; no actual exchange order endpoint."""
        from psycopg.types.json import Jsonb
        with self.cn.transaction():
            row=self.cn.execute('SELECT cash_jpy,btc_qty FROM portfolios WHERE name=%s FOR UPDATE',
                                (portfolio,)).fetchone()
            if row is None:raise ValueError('missing portfolio')
            cash,held=row
            if self.latest_bar_decided(portfolio,bar):return 'already_processed'
            reason=signal.reason;action='NOOP'
            if signal.valid:
                if self.paused():reason='user_paused';action='SKIP'
                elif held>0:reason='already_long';action='SKIP'
                elif portfolio=='news_gate' and news_state!='clear':
                    reason='news_'+news_state+':'+news_reason[:100];action='SKIP'
                elif not asks:reason='quote_unavailable';action='SKIP'
                else:
                    fill=buy_from_asks(asks,min(cash,MAX_BUY)) if cash>=Decimal('0.001') else None
                    if fill is None:reason='minimum_quantity_or_depth';action='SKIP'
                    else:
                        stop,target=bracket(fill.price,Decimal(str(signal.atr)))
                        self.cn.execute('''UPDATE portfolios SET cash_jpy=cash_jpy-%s,
                          btc_qty=%s,entry_cost_jpy=%s,entry_price=%s,stop_price=%s,target_price=%s,
                          entry_at=now(),updated_at=now() WHERE name=%s''',
                          (fill.jpy,fill.qty,fill.jpy,fill.price,stop,target,portfolio))
                        self.cn.execute('''INSERT INTO paper_fills(portfolio,side,price,qty,total_jpy,reason)
                          VALUES(%s,'BUY',%s,%s,%s,%s)''',
                          (portfolio,fill.price,fill.qty,fill.jpy,'2h_signal'))
                        action='BUY';reason='paper_buy_only'
            self.cn.execute('''INSERT INTO decisions(portfolio,bar_start,signal,action,reason,snapshot)
                VALUES(%s,%s,%s,%s,%s,%s)''',
                (portfolio,bar,signal.valid,action,reason,Jsonb(signal.snapshot)))
        return action

    def exit_on_tick(self,market_price:Decimal,bids:list|None)->list[str]:
        """Stop/target checked independently of news and two-hour entry timing."""
        actions=[]
        if not bids:return actions
        with self.cn.transaction():
            for name in ('baseline','news_gate'):
                row=self.cn.execute('''SELECT btc_qty,entry_cost_jpy,stop_price,target_price
                     FROM portfolios WHERE name=%s FOR UPDATE''',(name,)).fetchone()
                if row is None or row[0]<=0:continue
                qty,cost,stop,target=row
                reason='STOP' if market_price<=stop else ('TARGET' if market_price>=target else None)
                if not reason:continue
                fill=sell_to_bids(bids,qty)
                if fill is None:continue
                pnl=fill.jpy-cost
                self.cn.execute('''UPDATE portfolios SET cash_jpy=cash_jpy+%s,
                    btc_qty=0,entry_cost_jpy=0,entry_price=NULL,stop_price=NULL,
                    target_price=NULL,entry_at=NULL,realized_jpy=realized_jpy+%s,
                    updated_at=now() WHERE name=%s''',(fill.jpy,pnl,name))
                self.cn.execute('''INSERT INTO paper_fills(portfolio,side,price,qty,total_jpy,pnl_jpy,reason)
                    VALUES(%s,'SELL',%s,%s,%s,%s,%s)''',
                    (name,fill.price,fill.qty,fill.jpy,pnl,reason))
                actions.append(name+':'+reason)
        return actions
