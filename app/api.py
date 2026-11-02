"""Bearer-token protected GET dashboard API and POST request to pause new paper buys."""
from __future__ import annotations
import hmac,os
from datetime import datetime,timezone
from decimal import Decimal
from typing import Annotated

from fastapi import FastAPI,Depends,HTTPException,Request,status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer,HTTPAuthorizationCredentials
from pydantic import BaseModel,StrictBool

from .config import Settings
from .store import Store

app=FastAPI(title='Coincheck AI Paper Bot',version='0.1.0',docs_url=None,redoc_url=None,openapi_url=None)
auth=HTTPBearer(auto_error=False)


def load_key():
    key=os.getenv('DASHBOARD_TOKEN','')
    if len(key)<40 or key.startswith('REPLACE_'):
        raise RuntimeError('Strong DASHBOARD_TOKEN required')
    return key


def protect(cred:Annotated[HTTPAuthorizationCredentials|None,Depends(auth)]):
    expected=load_key()
    if not cred or cred.scheme.lower()!='bearer' or not hmac.compare_digest(cred.credentials,expected):
        raise HTTPException(status_code=401,detail='Unauthorized',headers={'WWW-Authenticate':'Bearer'})
    return True


def json_value(x):
    if isinstance(x,datetime):return x.isoformat()
    if isinstance(x,Decimal):return str(x)
    if isinstance(x,dict):return {k:json_value(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)):return [json_value(v) for v in x]
    return x


@app.on_event('startup')
def startup():
    Settings.load();load_key()


@app.get('/healthz')
def health():
    try:
        db=Store()
        try:db.cn.execute('SELECT 1')
        finally:db.close()
        return {'status':'ok','mode':'DRY_RUN_ONLY'}
    except Exception:
        return JSONResponse({'status':'db_unavailable'},status_code=503)


@app.get('/api/status',dependencies=[Depends(protect)])
def dashboard():
    db=Store()
    try:
        portfolios=db.cn.execute('''SELECT name,cash_jpy,btc_qty,entry_cost_jpy,entry_price,
           stop_price,target_price,realized_jpy,updated_at FROM portfolios ORDER BY name''').fetchall()
        items=[dict(zip(('name','cash_jpy','btc_qty','entry_cost_jpy','entry_price','stop_price',
                         'target_price','realized_jpy','updated_at'),x)) for x in portfolios]
        tick=db.cn.execute('SELECT COUNT(*),MAX(trade_at),MAX(price) FILTER(WHERE trade_at=(SELECT MAX(trade_at) FROM market_ticks)) FROM market_ticks').fetchone()
        session=db.cn.execute('''SELECT connected_at,last_trade_at,disconnected_at FROM feed_sessions ORDER BY connected_at DESC LIMIT 1''').fetchone()
        latest_news=db.latest_news()
        paused=db.paused()
        result={'mode':'DRY_RUN_ONLY','engine':'2h EMA21/55 RSI9/55 window3 + 4h EMA50/200 + ATR3/12',
                'buy_budget_jpy':'50000','source':'Coincheck public BTC/JPY',
                'ticks':tick[0],'last_trade_at':tick[1], 'last_trade_price':tick[2],
                'session':dict(zip(('connected_at','last_trade_at','disconnected_at'),session)) if session else None,
                'pause_new_buys':paused,'portfolios':items,
                'news':dict(zip(('state','summary','expires_at','sources','evaluated_at'),latest_news)) if latest_news else None,
                'server_time':datetime.now(timezone.utc)}
        return json_value(result)
    finally:db.close()


@app.get('/api/decisions',dependencies=[Depends(protect)])
def decisions(limit:int=30):
    db=Store()
    try:
        rows=db.cn.execute('''SELECT portfolio,bar_start,decided_at,signal,action,reason,snapshot
            FROM decisions ORDER BY decided_at DESC LIMIT %s''',(min(max(limit,1),100),)).fetchall()
        keys=('portfolio','bar_start','decided_at','signal','action','reason','snapshot')
        return json_value([dict(zip(keys,x)) for x in rows])
    finally:db.close()


@app.get('/api/fills',dependencies=[Depends(protect)])
def fills(limit:int=30):
    db=Store()
    try:
        rows=db.cn.execute('''SELECT portfolio,at,side,price,qty,total_jpy,pnl_jpy,reason
            FROM paper_fills ORDER BY at DESC LIMIT %s''',(min(max(limit,1),100),)).fetchall()
        return json_value([dict(zip(('portfolio','at','side','price','qty','total_jpy','pnl_jpy','reason'),x)) for x in rows])
    finally:db.close()


@app.get('/api/candles',dependencies=[Depends(protect)])
def candles(timeframe:int=120,limit:int=120):
    if timeframe not in (120,240):raise HTTPException(status_code=422,detail='Only 120/240 minutes')
    db=Store()
    try:
        rows=db.cn.execute('''SELECT bar_start,open,high,low,close,volume
            FROM candles WHERE timeframe_min=%s ORDER BY bar_start DESC LIMIT %s''',
            (timeframe,min(max(limit,1),300))).fetchall()
        return json_value([dict(zip(('start','open','high','low','close','volume'),r)) for r in reversed(rows)])
    finally:db.close()


@app.get('/api/news',dependencies=[Depends(protect)])
def news(limit:int=20):
    db=Store()
    try:
        rows=db.cn.execute('''SELECT at,state,summary,sources,expires_at,model
            FROM news_assessments ORDER BY at DESC LIMIT %s''',(min(max(limit,1),100),)).fetchall()
        return json_value([dict(zip(('at','state','summary','sources','expires_at','model'),r)) for r in rows])
    finally:db.close()


class PauseRequest(BaseModel):
    paused:StrictBool


@app.post('/api/control/pause',dependencies=[Depends(protect)])
def pause(body:PauseRequest):
    # Request affects new PAPER purchases only; never forces a sale.
    db=Store()
    try:db.set_paused(body.paused)
    finally:db.close()
    return {'pause_new_buys':body.paused,'mode':'DRY_RUN_ONLY'}
