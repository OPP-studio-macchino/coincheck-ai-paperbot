"""IN ISOLATED CLONED DATABASE ONLY — DB roles, migration and paper race checks.

Never run against the original live Compose/volume. Test modifies paper fixtures.
"""
from __future__ import annotations
import os,uuid,threading,time
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from psycopg import errors
from psycopg.types.json import Jsonb
from .config import Settings,db_connect
from .store import Store,bar_start
from .strategy import Signal
from .migrate import _connect_admin,upgrade,rollback_permissions

def expect_denied(role,sql_query):
    conn=db_connect(role)
    try:
        try:
            with conn.transaction():
                conn.execute(sql_query)
                raise AssertionError("PRIVILEGE_LEAK:"+role)
        except errors.InsufficientPrivilege:
            pass
    finally:conn.close()

def main():
    if os.environ.get("SECURITY_ISOLATED_TEST")!="YES_CLONE":
        raise RuntimeError("SECURITY_ISOLATED_TEST=YES_CLONE required")
    if os.environ.get("NEWS_MODE")!="mock":
        raise RuntimeError("mock only")
    s=Settings.load()
    admin,name,admin_name=_connect_admin()
    try:
        original=admin.execute("""SELECT name,cash_jpy,btc_qty,entry_cost_jpy,stop_price,target_price
                                  FROM portfolios ORDER BY name""").fetchall()
        assert len(original)==2
        baseline_before=original[0][1]
        print("ROLE_AUDIT_BEGIN")
        for role in ("query","control","news","market"):
            cn=db_connect(role)
            try:
                current=cn.execute("SELECT current_user").fetchone()[0]
                assert current=="paper_"+role,current
                assert not cn.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()[0]
            finally:cn.close()
        expect_denied("query","UPDATE portfolios SET cash_jpy=cash_jpy WHERE name='baseline'")
        expect_denied("query","INSERT INTO admin_audit(actor_id,request_id,action,previous,next_state,result,reason) VALUES('x',gen_random_uuid(),'a','{}','{}','ok','x')")
        expect_denied("control","UPDATE portfolios SET cash_jpy=cash_jpy WHERE name='baseline'")
        expect_denied("control","UPDATE market_ticks SET price=price")
        expect_denied("news","UPDATE portfolios SET btc_qty=btc_qty")
        expect_denied("news","INSERT INTO paper_fills(portfolio,side,price,qty,total_jpy,reason) VALUES('baseline','BUY',8000000,0.002,16000,'bad')")
        expect_denied("market","ALTER TABLE portfolios ADD COLUMN bad_flag_test INTEGER")
        expect_denied("news","CREATE TABLE public.bad_privilege_test(x int)")
        expect_denied("query","CREATE TABLE public.bad_privilege_test(x int)")
        expect_denied("control","CREATE TABLE public.bad_privilege_test(x int)")
        print("ROLE_DENIALS_PASS")

        ctrl=Store("control")
        market=Store("market")
        try:
            assert ctrl.paused() is False
            event=ctrl.set_paused(True,"security_test",str(uuid.uuid4()),"test_pause")
            assert event["paused"] is True
            assert ctrl.paused() is True
            aud=admin.execute("SELECT result,previous,next_state FROM admin_audit WHERE request_id=%s",
                              (event["request_id"],)).fetchone()
            assert aud and aud[0]=="success" and aud[1]["paused"] is False and aud[2]["paused"] is True
            print("PAUSE_AUDIT_TRANSACTION_PASS")
            # Simulated historical bar evaluated 2 mins after closure; no network data.
            bar=bar_start(datetime.now(timezone.utc),120)-timedelta(hours=2)
            now=bar+timedelta(hours=2,minutes=2)
            sig=Signal(True,"fixture_signal",100000.,{"fixture":True})
            asks=[(Decimal("8000000"),Decimal("1"))]
            before_fills=admin.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]
            outcome=market.apply_signal("baseline",bar,sig,asks,"unknown","unverified",
                                        Settings(Decimal("13000"),s.initial_jpy,"mock",Decimal("25"),s.git_sha),
                                        evaluated_at=now)
            assert outcome=="SKIP",outcome
            assert admin.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]==before_fills
            ctrl.set_paused(False,"security_test",str(uuid.uuid4()),"test_reopen")
            # Resumption never makes a prior rejected decision eligible again.
            again=market.apply_signal("baseline",bar,sig,asks,"clear","fixture",s,evaluated_at=now)
            assert again=="already_processed"
            print("PAUSE_RESTART_NO_REPLAY_PASS")

            # Concurrent stop: hold the control row, let a simulated buyer race,
            # commit stop and assert the waiting buy cannot pass after pause ACK.
            nextbar=bar+timedelta(hours=2)
            nextnow=nextbar+timedelta(hours=2,minutes=2)
            done=threading.Event()
            result=[]
            def try_buy():
                separate=Store("market")
                try:
                    result.append(separate.apply_signal("baseline",nextbar,sig,asks,
                        "unknown","fixture",s,evaluated_at=nextnow))
                except Exception as exc:
                    result.append(type(exc).__name__)
                finally:separate.close();done.set()
            with ctrl.cn.transaction():
                ctrl.cn.execute("SELECT pg_advisory_xact_lock(713446)")
                ctrl.cn.execute("SELECT paused FROM control_state WHERE id=1 FOR UPDATE")
                thread=threading.Thread(target=try_buy,daemon=True)
                thread.start()
                time.sleep(0.2)
                assert not done.is_set(),"Buyer did not wait for control lock"
                ctrl.cn.execute("UPDATE control_state SET paused=true,updated_at=now() WHERE id=1")
                req=str(uuid.uuid4())
                ctrl.cn.execute("""INSERT INTO admin_audit(actor_id,request_id,action,previous,next_state,result,reason)
                       VALUES('security_test',%s,'pause_new_buys',%s,%s,'success','concurrency_fixture')""",
                       (req,Jsonb({"paused":False}),Jsonb({"paused":True})))
            thread.join(timeout=10)
            assert done.is_set() and result==["SKIP"],result
            assert admin.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]==before_fills
            print("STOP_ACK_RACE_PASS")
            ctrl.set_paused(False,"security_test",str(uuid.uuid4()),"restore_control")
        finally:ctrl.close();market.close()

        # Runtime cannot modify existing paper balances on schema upgrade.
        unchanged=admin.execute("""SELECT name,cash_jpy,btc_qty,entry_cost_jpy,stop_price,target_price
                                    FROM portfolios ORDER BY name""").fetchall()
        assert original==unchanged,(original,unchanged)
        print("PORTFOLIO_PRESERVATION_PASS")

        # Clone-only non-destructive permission rollback/re-upgrade.
        rollback_permissions(admin,name,admin_name)
        assert admin.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0]==2
        upgrade(admin,name,admin_name)
        assert admin.execute("""SELECT name,cash_jpy,btc_qty,entry_cost_jpy,stop_price,target_price
                                FROM portfolios ORDER BY name""").fetchall()==original
        print("MIGRATION_ROLLBACK_REUPGRADE_PASS")
        print("ISOLATED_DB_SECURITY_E2E_PASS")
    finally:admin.close()

if __name__=="__main__":main()
