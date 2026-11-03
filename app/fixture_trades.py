"""Synthetic paper-fill assertions against a dedicated disposable named TEST DB.

NO network data, NO actual order API; test DB must start empty, separate from
the running paperbot. Prints only counts and verdicts, never credentials.
"""
from __future__ import annotations
import os
import uuid
import threading
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from .config import Settings
from .strategy import Signal
from .store import Store,bar_start

def require_isolated():
    target=os.environ.get("POSTGRES_DB","")
    if os.environ.get("SECURITY_ISOLATED_TEST")!="YES_CLONE" or not target.startswith("paperbot_test_"):
        raise RuntimeError("STOP: synthetic tests require a dedicated paperbot_test_* DB")
    return target

def main():
    dbname=require_isolated()
    base=Settings.load()
    market=Store("market");control=Store("control")
    try:
        initial=market.cn.execute("""SELECT name,cash_jpy,btc_qty FROM portfolios ORDER BY name""").fetchall()
        assert len(initial)==2 and all(float(x[1])==100000 and x[2]==0 for x in initial),initial
        assert market.cn.execute("SELECT count(*) FROM decisions").fetchone()[0]==0
        assert market.cn.execute("SELECT count(*) FROM paper_fills").fetchone()[0]==0
        assert control.paused() is False
        bar=bar_start(datetime.now(timezone.utc),120)-timedelta(hours=2)
        now=bar+timedelta(hours=2,minutes=2)
        asks=[(Decimal("8000000"),Decimal("1"))]
        bids=[(Decimal("7990000"),Decimal("1"))]
        setting=Settings(Decimal("13000"),base.initial_jpy,"mock",Decimal("25"),base.git_sha)
        sig=Signal(True,"synthetic_valid_buy",Decimal("50000"),{"rsi_window_age":0,"test_fixture":True})
        outcome=market.apply_signal("baseline",bar,sig,asks,"unknown","test",setting,evaluated_at=now)
        assert outcome=="BUY",outcome
        gate=market.apply_signal("news_gate",bar,sig,asks,"unknown","test",setting,evaluated_at=now)
        assert gate=="SKIP",gate
        assert market.apply_signal("baseline",bar,sig,asks,"unknown","test",setting,evaluated_at=now)=="already_processed"
        p=market.cn.execute("""SELECT cash_jpy,btc_qty,entry_cost_jpy,entry_price,
                stop_price,target_price FROM portfolios WHERE name='baseline'""").fetchone()
        assert p[1]>=Decimal("0.001") and p[2]<=Decimal("13000")
        assert p[0] + p[2] == Decimal("100000")
        assert p[4] == p[3]-Decimal("150000")
        assert p[5] == p[3]+Decimal("600000")
        assert market.cn.execute("SELECT COUNT(*) FROM paper_fills WHERE portfolio='baseline' AND side='BUY'").fetchone()[0]==1
        assert market.cn.execute("SELECT btc_qty FROM portfolios WHERE name='news_gate'").fetchone()[0]==0
        print("BUY_13000_INCLUSIVE_FEES_NEWS_UNKNOWN_NO_DUP_PASS")

        # News/control pause must never stop simulated ATR exit evaluation.
        paused=control.set_paused(True,"test_admin",str(uuid.uuid4()),"fixture_exit_under_pause")
        assert paused["paused"] is True
        out=market.exit_on_tick(p[4]-Decimal("10000"),bids,Decimal("25"))
        assert out==["baseline:STOP"],out
        assert market.cn.execute("SELECT btc_qty FROM portfolios WHERE name='baseline'").fetchone()[0]==0
        assert market.cn.execute("SELECT count(*) FROM paper_fills WHERE portfolio='baseline' AND side='SELL'").fetchone()[0]==1
        print("ATR_EXIT_WHILE_PAUSED_PASS")
        control.set_paused(False,"test_admin",str(uuid.uuid4()),"resume_for_cross_check")
        # Same RSI cross consumed despite closing the prior position: must SKIP.
        bar2=bar+timedelta(hours=2)
        sig_age1=Signal(True,"synthetic_old_cross",Decimal("50000"),{"rsi_window_age":1})
        again=market.apply_signal("baseline",bar2,sig_age1,asks,"unknown","test",setting,
                                  evaluated_at=bar2+timedelta(hours=2,minutes=2))
        assert again=="SKIP"
        why=market.cn.execute("SELECT reason FROM decisions WHERE portfolio='baseline' AND bar_start=%s",
                              (bar2,)).fetchone()[0]
        assert why=="rsi_cross_already_consumed",why
        assert market.cn.execute("SELECT count(*) FROM paper_fills WHERE portfolio='baseline' AND side='BUY'").fetchone()[0]==1
        print("RSI_WINDOW_REENTRY_PREVENTED_PASS")

        # High price with low budget fails minimum BTC, no new fill.
        bar3=bar+timedelta(hours=4)
        sig_new=Signal(True,"synthetic_new_cross",Decimal("50000"),{"rsi_window_age":0})
        low=market.apply_signal("baseline",bar3,sig_new,
            [(Decimal("20000000"),Decimal("1"))],"unknown","test",setting,
            evaluated_at=bar3+timedelta(hours=2,minutes=2))
        assert low=="SKIP"
        why=market.cn.execute("SELECT reason FROM decisions WHERE portfolio='baseline' AND bar_start=%s",
                              (bar3,)).fetchone()[0]
        assert why=="minimum_quantity_or_depth_or_cash",why
        print("MIN_QTY_PRICE_CONSTRAINT_PASS")
        assert market.cn.execute("SELECT count(*) FROM paper_fills").fetchone()[0]==2
        # Duplicate trade ID is persisted once even across collector sessions.
        from .market import TradeTick
        tick=TradeTick("fixture_"+uuid.uuid4().hex,datetime.now(timezone.utc),
                       Decimal("8000000"),Decimal("0.002"))
        session=market.connected()
        unique=market.ingest(session,[tick,tick])
        assert len(unique)==1
        assert market.ingest(session,[tick])==[]
        market.disconnected(session,"fixture_end")
        fresh_session=market.connected()
        assert market.ingest(fresh_session,[tick])==[]
        market.disconnected(fresh_session,"fixture_end")
        assert market.cn.execute("SELECT COUNT(*) FROM market_ticks WHERE trade_id=%s",(tick.trade_id,)).fetchone()[0]==1
        print("TRADE_ID_DEDUPE_ACROSS_SESSIONS_PASS")

        # Two concurrent workers must not create two BUYs from one bar.
        bar4=bar+timedelta(hours=6)
        now4=bar4+timedelta(hours=2,minutes=2)
        setting50=Settings(Decimal("50000"),base.initial_jpy,"mock",Decimal("25"),base.git_sha)
        outcome_list=[]
        def candidate_buy():
            conn=Store("market")
            try:
                result=conn.apply_signal("baseline",bar4,
                    Signal(True,"fresh_cross",Decimal("50000"),{"rsi_window_age":0}),
                    asks,"unknown","fixture",setting50,evaluated_at=now4)
                outcome_list.append(result)
            finally:conn.close()
        workers=[threading.Thread(target=candidate_buy,daemon=True) for _ in range(2)]
        for t in workers:t.start()
        for t in workers:t.join(timeout=10)
        assert all(not t.is_alive() for t in workers)
        assert sorted(outcome_list)==["BUY","already_processed"],outcome_list
        assert market.cn.execute("SELECT COUNT(*) FROM paper_fills WHERE portfolio='baseline' AND side='BUY'").fetchone()[0]==2
        spent=market.cn.execute("SELECT entry_cost_jpy FROM portfolios WHERE name='baseline'").fetchone()[0]
        assert Decimal("0")<spent<=Decimal("50000")
        print("CONCURRENT_SINGLE_BUY_50000_WITH_FEES_PASS")
        print("SYNTHETIC_DB_TEST_PASS db_group=isolated_fixture")
    finally:market.close();control.close()

if __name__=="__main__":main()
