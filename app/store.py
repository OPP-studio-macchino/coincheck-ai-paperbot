"""Role-scoped PostgreSQL access; atomic paper fills, audit and session continuity."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from .config import (Settings,db_connect,STRATEGY_VERSION,VERSION,
                     LOCAL_REQUIRED,UPPER_REQUIRED)
from .strategy import Candle,Signal
from .market import TradeTick
from .paper import buy_from_asks,sell_to_bids,bracket
import uuid

UTC=timezone.utc
def utc_now():return datetime.now(UTC)

def bar_start(at:datetime,minutes:int)->datetime:
    if at.tzinfo is None:raise ValueError("timezone required")
    seconds=int(at.timestamp());size=minutes*60
    return datetime.fromtimestamp((seconds//size)*size,UTC)

def ceil_bar(at:datetime,minutes:int)->datetime:
    b=bar_start(at,minutes)
    return b if b>=at else b+timedelta(minutes=minutes)

def trailing_contiguous(bars:list[Candle],minutes:int)->int:
    if not bars:return 0
    count=1
    for i in range(len(bars)-1,0,-1):
        if bars[i].start-bars[i-1].start != timedelta(minutes=minutes):break
        count+=1
    return count

class Store:
    def __init__(self,role:str="market"):
        self.role=role
        self.cn=db_connect(role,autocommit=True)
    def close(self):self.cn.close()

    def connected(self):
        session=str(uuid.uuid4())
        with self.cn.transaction():
            self.cn.execute("INSERT INTO feed_sessions(id,connected_at) VALUES(%s,%s)",(session,utc_now()))
        return session

    def disconnected(self,session:str,reason:str="disconnected"):
        self.cn.execute("UPDATE feed_sessions SET disconnected_at=now(),error_text=%s WHERE id=%s",
                        (reason[:120],session))

    def ingest(self,session:str,ticks:list[TradeTick])->list[TradeTick]:
        inserted=[]
        with self.cn.transaction():
            for t in ticks:
                exists=self.cn.execute("""INSERT INTO market_ticks(trade_id,trade_at,price,amount)
                       VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING trade_id""",
                       (t.trade_id,t.at,t.price,t.quantity)).fetchone()
                if not exists:continue
                inserted.append(t)
                for minutes in (120,240):
                    b=bar_start(t.at,minutes)
                    self.cn.execute("""INSERT INTO candles(timeframe_min,bar_start,open,high,low,close,
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
                          last_at=GREATEST(candles.last_at,EXCLUDED.last_at)""",
                        (minutes,b,t.price,t.price,t.price,t.price,t.quantity,t.at,t.at))
            if inserted:
                self.cn.execute("UPDATE feed_sessions SET last_trade_at=%s WHERE id=%s",
                                (max(t.at for t in inserted),session))
        return inserted

    def current_session(self):
        return self.cn.execute("""SELECT connected_at,last_trade_at FROM feed_sessions
             WHERE disconnected_at IS NULL ORDER BY connected_at DESC LIMIT 1""").fetchone()

    def closed_candles(self,minutes:int,now:datetime,limit:int=450)->list[Candle]:
        if minutes not in (120,240):raise ValueError("Unsupported timeframe")
        session=self.current_session()
        if not session or not session[1]:return []
        connected,last_trade=session
        if last_trade>now+timedelta(seconds=10) or now-last_trade>timedelta(seconds=120):
            return []
        safe_start=ceil_bar(connected,minutes)
        safe_end=bar_start(now-timedelta(seconds=120),minutes)
        rows=self.cn.execute("""SELECT bar_start,open,high,low,close,volume
            FROM candles WHERE timeframe_min=%s AND bar_start>=%s
              AND bar_start+make_interval(mins=>%s)<=%s
            ORDER BY bar_start DESC LIMIT %s""",
            (minutes,safe_start,minutes,safe_end,limit)).fetchall()
        return [Candle(r[0],*(float(x) for x in r[1:])) for r in reversed(rows)]

    def history_status(self,now:datetime):
        sess=self.current_session()
        active=bool(sess and sess[1] and
                    timedelta(0)<=now-sess[1]<=timedelta(seconds=120))
        result={"market_fresh":active,"history_ready":False,"reason":None,
                "2h_confirmed_contiguous":0,"4h_confirmed_contiguous":0,
                "2h_required":LOCAL_REQUIRED,"4h_required":UPPER_REQUIRED}
        if not active:
            result["reason"]="no_live_session_or_market_stale"
            return result
        lower=self.closed_candles(120,now)
        higher=self.closed_candles(240,now)
        result["2h_confirmed_contiguous"]=trailing_contiguous(lower,120)
        result["4h_confirmed_contiguous"]=trailing_contiguous(higher,240)
        safe_cutoff=bar_start(now-timedelta(seconds=120),120)
        if not lower or lower[-1].start+timedelta(minutes=120)!=safe_cutoff:
            result["reason"]="latest_confirmed_2h_missing_or_incomplete"
        elif result["2h_confirmed_contiguous"]<LOCAL_REQUIRED:
            result["reason"]="2h_warmup_incomplete"
        elif result["4h_confirmed_contiguous"]<UPPER_REQUIRED:
            result["reason"]="4h_warmup_incomplete"
        else:
            result["history_ready"]=True
            result["reason"]="all_conditions_met"
        return result

    def latest_news(self):
        return self.cn.execute("""SELECT state,summary,expires_at,sources,at
                 FROM news_assessments ORDER BY at DESC LIMIT 1""").fetchone()

    def record_news(self,state:str,summary:str,sources:list,expires_at:datetime,model:str,usage:dict):
        from psycopg.types.json import Jsonb
        if state not in ("clear","block","unknown"):raise ValueError("bad risk state")
        self.cn.execute("""INSERT INTO news_assessments(state,summary,sources,expires_at,model,usage)
            VALUES(%s,%s,%s,%s,%s,%s)""",
            (state,summary[:1500],Jsonb(sources),expires_at,model,Jsonb(usage)))

    def reserve_news_attempt(self,limit:int=48):
        """Durably count ALL attempts before any billable call (including failures)."""
        with self.cn.transaction():
            self.cn.execute("SELECT pg_advisory_xact_lock(713445)")
            count=self.cn.execute("""SELECT COUNT(*) FROM news_attempts
                 WHERE requested_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'""").fetchone()[0]
            if count>=limit:return None
            return self.cn.execute("""INSERT INTO news_attempts(status)
                  VALUES('reserved') RETURNING id""").fetchone()[0]

    def settle_news_attempt(self,attempt_id:int,success:bool):
        self.cn.execute("""UPDATE news_attempts SET status=%s,completed_at=now()
                  WHERE id=%s AND status='reserved'""",
                  ("succeeded" if success else "failed",attempt_id))

    def log_error(self,subsystem:str,message:str):
        # Callers must use constant error codes/class names, not error strings/secrets.
        self.cn.execute("INSERT INTO errors(subsystem,message) VALUES(%s,%s)",
                        (subsystem[:40],message[:100]))

    def paused(self):
        return self.cn.execute("SELECT paused FROM control_state WHERE id=1").fetchone()[0]

    def set_paused(self,paused:bool,actor_id:str,request_id:str,reason:str="operator_request"):
        """Pause acknowledgement is linearizable with buys on control_state row lock."""
        from psycopg.types.json import Jsonb
        if type(paused) is not bool or len(actor_id)>60 or len(reason)>140:
            raise ValueError("Invalid control request")
        with self.cn.transaction():
            # Same transaction-level advisory lock as apply_signal; never return
            # "stopped" while a preceding simulated purchase could still commit.
            self.cn.execute("SELECT pg_advisory_xact_lock(713446)")
            before=self.cn.execute("SELECT paused FROM control_state WHERE id=1 FOR UPDATE").fetchone()
            if before is None:raise RuntimeError("Missing control_state singleton")
            old=bool(before[0])
            self.cn.execute("UPDATE control_state SET paused=%s,updated_at=now() WHERE id=1",
                            (paused,))
            self.cn.execute("""INSERT INTO admin_audit(request_id,actor_id,action,previous,next_state,result,reason)
                VALUES(%s,%s,'pause_new_buys',%s,%s,'success',%s)""",
                (request_id,actor_id,Jsonb({"paused":old}),Jsonb({"paused":paused}),reason))
        return {"previous":old,"paused":paused,"request_id":request_id}

    def record_control_denied(self,actor_id:str,request_id:str,reason:str):
        from psycopg.types.json import Jsonb
        with self.cn.transaction():
            before=self.cn.execute("SELECT paused FROM control_state WHERE id=1").fetchone()
            state={"paused":bool(before[0])} if before else {"paused":None}
            self.cn.execute("""INSERT INTO admin_audit(request_id,actor_id,action,previous,next_state,result,reason)
                 VALUES(%s,%s,'pause_new_buys',%s,%s,'denied',%s)""",
                 (request_id,actor_id,Jsonb(state),Jsonb(state),reason))

    def latest_bar_decided(self,portfolio:str,bar:datetime)->bool:
        return bool(self.cn.execute("SELECT 1 FROM decisions WHERE portfolio=%s AND bar_start=%s",
                                   (portfolio,bar)).fetchone())

    def set_runtime(self,s:Settings,*,market_at:datetime|None=None,
                    exit_at:datetime|None=None,exit_reason:str|None=None,
                    last_eval_at:datetime|None=None):
        """Market alone declares its effective runtime configuration / evaluation."""
        self.cn.execute("""INSERT INTO runtime_status(id,git_sha,strategy_version,config_version,
               buy_budget_jpy,initial_config_jpy,fee_bps,news_mode,market_at,exit_eval_at,exit_reason,last_signal_at,updated_at)
           VALUES(1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
           ON CONFLICT(id) DO UPDATE SET
             git_sha=EXCLUDED.git_sha,
             strategy_version=EXCLUDED.strategy_version,
             config_version=EXCLUDED.config_version,
             buy_budget_jpy=EXCLUDED.buy_budget_jpy,
             initial_config_jpy=EXCLUDED.initial_config_jpy,
             fee_bps=EXCLUDED.fee_bps,
             news_mode=EXCLUDED.news_mode,
             market_at=COALESCE(EXCLUDED.market_at,runtime_status.market_at),
             exit_eval_at=COALESCE(EXCLUDED.exit_eval_at,runtime_status.exit_eval_at),
             exit_reason=COALESCE(EXCLUDED.exit_reason,runtime_status.exit_reason),
             last_signal_at=COALESCE(EXCLUDED.last_signal_at,runtime_status.last_signal_at),
             updated_at=now()""",
          (s.git_sha,STRATEGY_VERSION,VERSION,s.buy_jpy,s.initial_jpy,s.fee_bps,
           s.news_mode,market_at,exit_at,exit_reason,last_eval_at))

    def apply_signal(self,portfolio:str,bar:datetime,signal:Signal,asks:list|None,
                     news_state:str,news_reason:str,settings:Settings,
                     *,evaluated_at:datetime|None=None)->str:
        """Atomic row locking: no BUY can commit after a successful pause acknowledgement."""
        from psycopg.types.json import Jsonb
        now=evaluated_at or utc_now()
        if bar.tzinfo is None or portfolio not in ("baseline","news_gate"):
            raise ValueError("Invalid decision")
        with self.cn.transaction():
            self.cn.execute("SELECT pg_advisory_xact_lock(713446)")
            ctrl=self.cn.execute("SELECT paused FROM control_state WHERE id=1").fetchone()
            if ctrl is None:raise RuntimeError("No control singleton")
            row=self.cn.execute("""SELECT cash_jpy,btc_qty FROM portfolios WHERE name=%s FOR UPDATE""",
                                (portfolio,)).fetchone()
            if row is None:raise RuntimeError("Missing portfolio")
            cash,held=row
            if self.latest_bar_decided(portfolio,bar):return "already_processed"
            reason=signal.reason
            action="NOOP"
            if signal.valid:
                # Exact new 2h bar only. No deferred buys after network loss / process restart.
                age=(now-(bar+timedelta(minutes=120))).total_seconds()
                if age<0 or age>600:reason="stale_or_future_signal";action="SKIP"
                elif ctrl[0]:reason="user_paused";action="SKIP"
                elif held>0:reason="already_long";action="SKIP"
                elif (not isinstance(signal.snapshot,dict) or
                      type(signal.snapshot.get("rsi_window_age")) is not int or
                      not 0<=signal.snapshot["rsi_window_age"]<=3):
                    reason="invalid_rsi_cross_reference";action="SKIP"
                elif self.cn.execute("""SELECT 1 FROM decisions
                    WHERE portfolio=%s AND action='BUY'
                    AND bar_start>=%s AND bar_start<=%s LIMIT 1""",
                    (portfolio,bar-timedelta(hours=2*signal.snapshot["rsi_window_age"]),bar)).fetchone():
                    # Even after selling, one RSI crossover can cause at most one BUY.
                    reason="rsi_cross_already_consumed";action="SKIP"
                elif portfolio=="news_gate" and news_state!="clear":
                    reason="news_unknown_or_block";action="SKIP"
                elif not asks:reason="quote_unavailable";action="SKIP"
                elif signal.atr is None:reason="missing_atr";action="SKIP"
                else:
                    effective_budget=min(cash,settings.buy_jpy)
                    fill=buy_from_asks(asks,effective_budget,settings.fee_bps) if effective_budget>0 else None
                    if fill is None:reason="minimum_quantity_or_depth_or_cash";action="SKIP"
                    else:
                        stop,target=bracket(fill.price,Decimal(str(signal.atr)))
                        self.cn.execute("""UPDATE portfolios SET cash_jpy=cash_jpy-%s,
                            btc_qty=%s,entry_cost_jpy=%s,entry_price=%s,stop_price=%s,
                            target_price=%s,entry_at=now(),updated_at=now() WHERE name=%s""",
                            (fill.total_buy_jpy,fill.qty,fill.total_buy_jpy,fill.price,
                             stop,target,portfolio))
                        self.cn.execute("""INSERT INTO paper_fills(portfolio,side,price,qty,total_jpy,reason)
                            VALUES(%s,'BUY',%s,%s,%s,'2h_signal')""",
                            (portfolio,fill.price,fill.qty,fill.total_buy_jpy))
                        action="BUY";reason="paper_buy_only"
            self.cn.execute("""INSERT INTO decisions(portfolio,bar_start,signal,action,reason,snapshot)
                 VALUES(%s,%s,%s,%s,%s,%s)""",
                 (portfolio,bar,signal.valid,action,reason,Jsonb(signal.snapshot)))
        return action

    def exit_on_tick(self,market_price:Decimal,bids:list|None,
                     fee_bps:Decimal=Decimal("0"))->list[str]:
        """Exit evaluation is separate from new-buy pause/news, with row locks."""
        actions=[]
        if not bids:return actions
        with self.cn.transaction():
            for name in ("baseline","news_gate"):
                row=self.cn.execute("""SELECT btc_qty,entry_cost_jpy,stop_price,target_price
                    FROM portfolios WHERE name=%s FOR UPDATE""",(name,)).fetchone()
                if not row or row[0]<=0:continue
                qty,cost,stop,target=row
                reason="STOP" if market_price<=stop else ("TARGET" if market_price>=target else None)
                if not reason:continue
                fill=sell_to_bids(bids,qty,fee_bps)
                if fill is None:continue
                proceeds=fill.net_sell_jpy
                pnl=proceeds-cost
                self.cn.execute("""UPDATE portfolios SET cash_jpy=cash_jpy+%s,
                    btc_qty=0,entry_cost_jpy=0,entry_price=NULL,stop_price=NULL,
                    target_price=NULL,entry_at=NULL,realized_jpy=realized_jpy+%s,
                    updated_at=now() WHERE name=%s""",(proceeds,pnl,name))
                self.cn.execute("""INSERT INTO paper_fills(portfolio,side,price,qty,total_jpy,pnl_jpy,reason)
                    VALUES(%s,'SELL',%s,%s,%s,%s,%s)""",
                    (name,fill.price,fill.qty,proceeds,pnl,reason))
                actions.append(name+":"+reason)
        return actions
