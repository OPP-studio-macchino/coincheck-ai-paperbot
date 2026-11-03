"""Versioned, non-destructive PostgreSQL security migration / permission rollback.

Explicit one-shot migration service only; never run on market, news or API startup.
Migration service is the ONLY service with schema admin credentials.
"""
from __future__ import annotations
import os
from pathlib import Path
import sys
from .config import Settings,read_secret

BASE_TABLES = (
    "market_ticks","candles","feed_sessions","control_state","portfolios",
    "decisions","paper_fills","news_assessments","errors"
)
NEW_TABLES = ("admin_audit","runtime_status","news_attempts","schema_migrations")
ALL_TABLES = BASE_TABLES + NEW_TABLES
ROLE_NAMES = ("paper_market","paper_news","paper_query","paper_control")
OWNER = "paper_schema_owner"
PASS_FILES = {
    "paper_market": "DB_MARKET_PASSWORD_FILE",
    "paper_news": "DB_NEWS_PASSWORD_FILE",
    "paper_query": "DB_QUERY_PASSWORD_FILE",
    "paper_control": "DB_CONTROL_PASSWORD_FILE",
}
MIGRATION = "001_security_roles_audit_runtime"

def _connect_admin():
    import psycopg
    name=os.environ.get("POSTGRES_DB","paperbot")
    admin=os.environ.get("POSTGRES_USER","paper_admin")
    host=os.environ.get("DB_HOST","db")
    if host not in ("db","localhost","127.0.0.1"):
        raise RuntimeError("Unexpected migration DB host")
    if not name.isidentifier() or not admin.isidentifier() or admin in ROLE_NAMES:
        raise RuntimeError("Bad migration database identity")
    password=read_secret("DB_ADMIN_PASSWORD_FILE",min_length=32)
    return psycopg.connect(host=host,dbname=name,user=admin,password=password,
                           connect_timeout=8,autocommit=True),name,admin

def _grant(conn,role:str,tables:str,permissions:str):
    from psycopg import sql
    for name in tables.split(","):
        conn.execute(sql.SQL("GRANT {} ON TABLE public.{} TO {}").format(
            sql.SQL(permissions),sql.Identifier(name.strip()),sql.Identifier(role)))

def _seq(conn,role:str,seqs:str):
    from psycopg import sql
    for name in seqs.split(","):
        conn.execute(sql.SQL("GRANT USAGE ON SEQUENCE public.{} TO {}").format(
            sql.Identifier(name.strip()),sql.Identifier(role)))

def upgrade(conn,name:str,admin:str):
    from psycopg import sql
    with conn.transaction():
        # Password-bearing DDL must not be echoed on a PostgreSQL error.
        conn.execute("SET LOCAL log_min_error_statement = 'panic'")
        conn.execute("SET LOCAL log_statement = 'none'")
        count=conn.execute("""SELECT count(*) FROM information_schema.tables
            WHERE table_schema='public' AND table_name = ANY(%s)""",
            (list(BASE_TABLES),)).fetchone()[0]
        if count != len(BASE_TABLES):
            raise RuntimeError("STOP source schema incomplete: migration cannot initialize balances")
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s",(OWNER,)).fetchone():
            conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                sql.Identifier(OWNER)))
        for role in ROLE_NAMES:
            p=read_secret(PASS_FILES[role],min_length=32)
            if conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s",(role,)).fetchone():
                conn.execute(sql.SQL("ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}").format(
                    sql.Identifier(role),sql.Literal(p)))
            else:
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}").format(
                    sql.Identifier(role),sql.Literal(p)))
        migration_sql=(Path(__file__).parents[1]/"migrations"/"001_security_roles_audit_runtime.sql").read_text()
        conn.execute(migration_sql)
        # Existing user data remains byte-for-byte; only owner/permissions change.
        for t in ALL_TABLES:
            conn.execute(sql.SQL("ALTER TABLE public.{} OWNER TO {}").format(
                sql.Identifier(t),sql.Identifier(OWNER)))
        for seq in ("decisions_id_seq","paper_fills_id_seq","news_assessments_id_seq",
                    "errors_id_seq","admin_audit_id_seq","news_attempts_id_seq"):
            conn.execute(sql.SQL("ALTER SEQUENCE public.{} OWNER TO {}").format(
                sql.Identifier(seq),sql.Identifier(OWNER)))
        conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        # FK triggers execute under table-owner privileges; that NOLOGIN
        # owner must retain USAGE on the schema even without any app login.
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(OWNER)))
        conn.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC")
        conn.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC")
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
        for role in ROLE_NAMES:
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(name),sql.Identifier(role)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}").format(sql.Identifier(role)))

        _grant(conn,"paper_query",",".join(ALL_TABLES),"SELECT")
        _grant(conn,"paper_control","control_state","SELECT")
        conn.execute("GRANT UPDATE (paused,updated_at) ON public.control_state TO paper_control")
        _grant(conn,"paper_control","admin_audit","INSERT")
        _seq(conn,"paper_control","admin_audit_id_seq")

        _grant(conn,"paper_news","news_assessments,news_attempts","SELECT,INSERT")
        conn.execute("GRANT UPDATE (status,completed_at) ON public.news_attempts TO paper_news")
        _seq(conn,"paper_news","news_assessments_id_seq,news_attempts_id_seq")

        _grant(conn,"paper_market","market_ticks,candles,feed_sessions,portfolios,decisions,paper_fills,control_state,news_assessments,runtime_status","SELECT")
        _grant(conn,"paper_market","market_ticks,candles,feed_sessions,decisions,paper_fills,runtime_status","INSERT")
        conn.execute("GRANT UPDATE (open,high,low,close,volume,trade_count,first_at,last_at) ON public.candles TO paper_market")
        conn.execute("GRANT UPDATE (last_trade_at,disconnected_at,error_text) ON public.feed_sessions TO paper_market")
        conn.execute("""GRANT UPDATE (cash_jpy,btc_qty,entry_cost_jpy,entry_price,
          stop_price,target_price,entry_at,realized_jpy,updated_at) ON public.portfolios TO paper_market""")
        conn.execute("""GRANT UPDATE (git_sha,strategy_version,config_version,buy_budget_jpy,
          initial_config_jpy,fee_bps,news_mode,market_at,exit_eval_at,exit_reason,last_signal_at,updated_at)
          ON public.runtime_status TO paper_market""")
        _seq(conn,"paper_market","decisions_id_seq,paper_fills_id_seq")
        for role in ("paper_market","paper_news"):
            _grant(conn,role,"errors","INSERT")
            _seq(conn,role,"errors_id_seq")
        conn.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC").format(sql.Identifier(OWNER)))
    print("MIGRATION_APPLIED",MIGRATION,"(no balances reset)")

def rollback_permissions(conn,name:str,admin:str):
    """Non-destructive permission rollback for cloned DB only; leaves new tables intact."""
    from psycopg import sql
    with conn.transaction():
        for role in ROLE_NAMES:
            conn.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}").format(sql.Identifier(role)))
        for t in ALL_TABLES:
            conn.execute(sql.SQL("ALTER TABLE public.{} OWNER TO {}").format(sql.Identifier(t),sql.Identifier(admin)))
        for seq in ("decisions_id_seq","paper_fills_id_seq","news_assessments_id_seq",
                    "errors_id_seq","admin_audit_id_seq","news_attempts_id_seq"):
            conn.execute(sql.SQL("ALTER SEQUENCE public.{} OWNER TO {}").format(sql.Identifier(seq),sql.Identifier(admin)))
        conn.execute("UPDATE public.schema_migrations SET status='permissions_rolled_back' WHERE version=%s",(MIGRATION,))
    print("MIGRATION_PERMISSIONS_ROLLED_BACK",MIGRATION,"(tables and balances retained)")

def main():
    Settings.load()
    if os.environ.get("NEWS_MODE","mock")!="mock":
        raise RuntimeError("migrations for this acceptance must use NEWS_MODE=mock")
    action=sys.argv[1] if len(sys.argv)>1 else "upgrade"
    if action not in ("upgrade","rollback-permissions"):
        raise RuntimeError("Invalid migration action")
    if action=="rollback-permissions" and os.environ.get("ALLOW_PERMISSION_ROLLBACK")!="CLONE_ONLY":
        raise RuntimeError("rollback must be explicitly authorized on a cloned DB")
    conn,name,admin=_connect_admin()
    try:
        if action=="upgrade":upgrade(conn,name,admin)
        else:rollback_permissions(conn,name,admin)
    finally:conn.close()

if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        # Never echo SQL text or server errors containing role passwords.
        print("MIGRATION_FAILED category="+type(exc).__name__,file=sys.stderr)
        raise SystemExit(2)
