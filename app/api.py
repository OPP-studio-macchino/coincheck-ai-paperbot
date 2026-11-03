"""Authenticated read-only dashboard + explicitly scoped audited pause control."""
from __future__ import annotations
import hmac,os,threading,time,uuid
from collections import defaultdict,deque
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from urllib.parse import urlsplit
from typing import Annotated

from fastapi import FastAPI,Depends,HTTPException,Request,Query
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer,HTTPAuthorizationCredentials
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel,StrictBool,ConfigDict,Field

from .config import Settings,read_secret
from .store import Store

app=FastAPI(title="Coincheck Paper Control",docs_url=None,redoc_url=None,openapi_url=None)
app.add_middleware(TrustedHostMiddleware,
                   allowed_hosts=["localhost","127.0.0.1"])
auth=HTTPBearer(auto_error=False)
_failed=defaultdict(deque)
_failed_lock=threading.Lock()
FAILED_LIMIT=8
FAILED_WINDOW_S=60

def _rate_guard(key:str,failed:bool=False):
    now=time.monotonic()
    with _failed_lock:
        q=_failed[key]
        while q and now-q[0]>FAILED_WINDOW_S:q.popleft()
        if len(q)>=FAILED_LIMIT:raise HTTPException(429,detail="Too many authentication failures")
        if failed:q.append(now)

def auth_role(request:Request,
              cred:Annotated[HTTPAuthorizationCredentials|None,Depends(auth)])->str:
    host=request.client.host if request.client else "unknown"
    viewer=read_secret("VIEW_TOKEN_FILE")
    admin=read_secret("ADMIN_TOKEN_FILE")
    if hmac.compare_digest(viewer,admin):
        raise RuntimeError("View and admin credentials must be different")
    provided=cred.credentials if cred and cred.scheme.lower()=="bearer" else ""
    is_admin=hmac.compare_digest(provided,admin)
    is_view=hmac.compare_digest(provided,viewer)
    if not (is_admin or is_view):
        _rate_guard(host,failed=True)
        raise HTTPException(401,detail="Unauthorized",headers={"WWW-Authenticate":"Bearer"})
    return "admin" if is_admin else "viewer"

def require_admin(request:Request,role:Annotated[str,Depends(auth_role)])->str:
    if role!="admin":
        try:
            db=Store("control")
            try:db.record_control_denied("viewer",request.state.request_id,"insufficient_permission")
            finally:db.close()
        except Exception:pass
        raise HTTPException(403,detail="Insufficient permissions")
    return role

def json_value(x):
    if isinstance(x,datetime):return x.isoformat()
    if isinstance(x,Decimal):return str(x)
    if isinstance(x,dict):return {k:json_value(v) for k,v in x.items()}
    if isinstance(x,(tuple,list)):return [json_value(v) for v in x]
    return x

@app.middleware("http")
async def boundary(request:Request,call_next):
    request.state.request_id=str(uuid.uuid4())
    # Never accept client-supplied forwarding headers for authorization/rate limits.
    host=request.headers.get("host","")
    if not host or len(host)>90:return JSONResponse({"detail":"Invalid Host"},status_code=400)
    if request.method in ("POST","PATCH","PUT","DELETE"):
        raw_origin=request.headers.get("origin")
        if request.headers.get("sec-fetch-site","").lower() not in ("","same-origin","none"):
            return JSONResponse({"detail":"Cross-site request denied"},status_code=403)
        if raw_origin:
            try:
                parsed=urlsplit(raw_origin)
                hostonly=host.split(":")[0].lower()
                if parsed.scheme!="http" or parsed.hostname!=hostonly or parsed.username or parsed.password:
                    return JSONResponse({"detail":"Origin mismatch"},status_code=403)
            except ValueError:
                return JSONResponse({"detail":"Bad Origin"},status_code=403)
        length=request.headers.get("content-length")
        try:
            if length and int(length)>2048:
                return JSONResponse({"detail":"Request too large"},status_code=413)
        except ValueError:
            return JSONResponse({"detail":"Bad length"},status_code=400)
        if len(await request.body())>2048:
            return JSONResponse({"detail":"Request too large"},status_code=413)
    response=await call_next(request)
    response.headers["X-Request-ID"]=request.state.request_id
    response.headers["Cache-Control"]="no-store"
    response.headers["X-Content-Type-Options"]="nosniff"
    return response

@app.on_event("startup")
def startup():
    Settings.load()
    view=read_secret("VIEW_TOKEN_FILE")
    admin=read_secret("ADMIN_TOKEN_FILE")
    if view==admin:raise RuntimeError("Admin/view tokens must differ")

@app.get("/healthz")
def health():
    try:
        db=Store("query")
        try:db.cn.execute("SELECT 1").fetchone()
        finally:db.close()
        return {"status":"ok"}  # Liveness/DB-only; NOT trading-ready
    except Exception:
        return JSONResponse({"status":"unavailable"},status_code=503)

@app.get("/api/status")
def dashboard(role:Annotated[str,Depends(auth_role)]):
    now=datetime.now(timezone.utc)
    db=Store("query")
    try:
        rows=db.cn.execute("""SELECT name,cash_jpy,btc_qty,entry_cost_jpy,
           entry_price,stop_price,target_price,realized_jpy,updated_at
           FROM portfolios ORDER BY name""").fetchall()
        keys=("name","cash_jpy","btc_qty","entry_cost_jpy","entry_price",
              "stop_price","target_price","realized_jpy","updated_at")
        portfolios=[dict(zip(keys,row)) for row in rows]
        tick=db.cn.execute("""SELECT trade_at,price FROM market_ticks
                ORDER BY trade_at DESC,trade_id DESC LIMIT 1""").fetchone()
        num=db.cn.execute("SELECT COUNT(*) FROM market_ticks").fetchone()[0]
        session=db.cn.execute("""SELECT connected_at,last_trade_at,disconnected_at
             FROM feed_sessions ORDER BY connected_at DESC LIMIT 1""").fetchone()
        latest_news=db.latest_news()
        runtime=db.cn.execute("""SELECT git_sha,strategy_version,config_version,
                 buy_budget_jpy,initial_config_jpy,fee_bps,news_mode,
                 market_at,exit_eval_at,exit_reason,last_signal_at,updated_at
                 FROM runtime_status WHERE id=1""").fetchone()
        rk=("git_sha","strategy_version","config_version","buy_budget_jpy",
            "initial_config_jpy","fee_bps","news_mode",
            "market_at","exit_eval_at","exit_reason","last_signal_at","updated_at")
        runtime=dict(zip(rk,runtime)) if runtime else None
        history=db.history_status(now)
        fresh=bool(tick and timedelta(0)<=now-tick[0]<=timedelta(seconds=120)
                   and history["market_fresh"])
        last_px=tick[1] if tick else None
        last_at=tick[0] if tick else None
        for p in portfolios:
            if fresh:
                btc=Decimal(p["btc_qty"])
                p["valuation_jpy"]=p["cash_jpy"]+btc*last_px
                p["unrealized_pnl_jpy"]=btc*last_px-p["entry_cost_jpy"] if btc else Decimal("0")
                p["valuation_status"]="fresh_public_last_trade"
            else:
                p["valuation_jpy"]=None
                p["unrealized_pnl_jpy"]=None
                p["valuation_status"]="unavailable_stale_or_disconnected"
        evaluation_fresh=bool(history["market_fresh"] and runtime
                 and runtime["exit_eval_at"] and timedelta(0)<=now-runtime["exit_eval_at"]<=timedelta(seconds=120))
        response={
          "mode":"DRY_RUN_ONLY",
          "actor_role":role,
          "real_coincheck_assets":"unconnected",
          "source":"Coincheck public exchange BTC/JPY",
          "ticks":num,"last_trade_at":last_at,"last_trade_price":last_px,
          "market_price_fresh":fresh,
          "valuation_method":"cash + BTC * last public trade; fee not deducted (mark-to-market only)",
          "session":dict(zip(("connected_at","last_trade_at","disconnected_at"),session)) if session else None,
          "pause_new_buys":db.paused(),"runtime":runtime,
          "effective_buy_budget_jpy":runtime["buy_budget_jpy"] if runtime else None,
          "history":history,
          "exit_evaluation":{"available":evaluation_fresh,
             "last_evaluated_at":runtime["exit_eval_at"] if runtime else None,
             "reason":runtime["exit_reason"] if runtime else "market_not_running"},
          "portfolios":portfolios,
          "news":dict(zip(("state","summary","expires_at","sources","evaluated_at"),latest_news)) if latest_news else None,
          "server_time":now
        }
        return json_value(response)
    finally:db.close()

@app.get("/api/decisions")
def decisions(role:Annotated[str,Depends(auth_role)],limit:int=Query(30,ge=1,le=100)):
    db=Store("query")
    try:
        rows=db.cn.execute("""SELECT portfolio,bar_start,decided_at,signal,action,reason,snapshot
               FROM decisions ORDER BY decided_at DESC LIMIT %s""",(limit,)).fetchall()
        return json_value([dict(zip(("portfolio","bar_start","decided_at","signal",
                                     "action","reason","snapshot"),r)) for r in rows])
    finally:db.close()

@app.get("/api/fills")
def fills(role:Annotated[str,Depends(auth_role)],limit:int=Query(30,ge=1,le=100)):
    db=Store("query")
    try:
        rows=db.cn.execute("""SELECT portfolio,at,side,price,qty,total_jpy,pnl_jpy,reason
             FROM paper_fills ORDER BY at DESC LIMIT %s""",(limit,)).fetchall()
        return json_value([dict(zip(("portfolio","at","side","price","qty",
                                     "total_jpy","pnl_jpy","reason"),r)) for r in rows])
    finally:db.close()

@app.get("/api/candles")
def candles(role:Annotated[str,Depends(auth_role)],
            timeframe:int=Query(120),limit:int=Query(120,ge=1,le=300)):
    if timeframe not in (120,240):raise HTTPException(422,detail="Unsupported timeframe")
    db=Store("query")
    try:
        rows=db.cn.execute("""SELECT bar_start,open,high,low,close,volume,
                    bar_start+make_interval(mins=>%s)<=now() AS clock_closed
                FROM candles WHERE timeframe_min=%s
                ORDER BY bar_start DESC LIMIT %s""",(timeframe,timeframe,limit)).fetchall()
        return json_value([dict(zip(("start","open","high","low","close",
                                     "volume","clock_closed_only"),r)) for r in reversed(rows)])
    finally:db.close()

@app.get("/api/news")
def news(role:Annotated[str,Depends(auth_role)],limit:int=Query(20,ge=1,le=100)):
    db=Store("query")
    try:
        rows=db.cn.execute("""SELECT at,state,summary,sources,expires_at,model
                   FROM news_assessments ORDER BY at DESC LIMIT %s""",(limit,)).fetchall()
        return json_value([dict(zip(("at","state","summary","sources","expires_at","model"),r)) for r in rows])
    finally:db.close()

@app.get("/api/audit")
def audit(role:Annotated[str,Depends(auth_role)],limit:int=Query(30,ge=1,le=100)):
    db=Store("query")
    try:
        rows=db.cn.execute("""SELECT at,actor_id,request_id,action,previous,next_state,result,reason
                  FROM admin_audit ORDER BY at DESC LIMIT %s""",(limit,)).fetchall()
        return json_value([dict(zip(("at","actor_id","request_id","action","previous",
                                     "next_state","result","reason"),r)) for r in rows])
    finally:db.close()

class PauseRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    paused:StrictBool
    reason:str=Field(default="operator_request",min_length=1,max_length=120)

@app.post("/api/control/pause")
def pause(body:PauseRequest,request:Request,role:Annotated[str,Depends(require_admin)]):
    db=Store("control")
    try:
        event=db.set_paused(body.paused,actor_id="single_admin",
                            request_id=request.state.request_id,reason=body.reason)
    finally:db.close()
    return {**event,"mode":"DRY_RUN_ONLY","note":"Affects new PAPER buys only; ATR exit checks continue"}
