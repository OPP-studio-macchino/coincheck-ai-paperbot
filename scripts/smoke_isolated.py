"""Isolated frontend/API acceptance. Never targets the live 8097 service."""
from __future__ import annotations
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import json,os,time
from dataclasses import dataclass
BASE="http://127.0.0.1:18098"
ROOT=Path(__file__).resolve().parents[1]

def assert_isolated():
    if os.getenv("SECURITY_ISOLATED_TEST")!="YES_CLONE":
        raise RuntimeError("Run only with SECURITY_ISOLATED_TEST=YES_CLONE")
    if (ROOT/".env").read_text().find("COMPOSE_PROJECT_NAME=coincheck-ai-security")<0:
        raise RuntimeError("Wrong compose project")
    if (ROOT/".env").read_text().find("FRONTEND_PORT=18098")<0:
        raise RuntimeError("Wrong local port")

def req(path:str,token:str|None=None,method:str="GET",body:dict|None=None,headers:dict|None=None):
    h={"Accept":"application/json"}
    if token:h["Authorization"]="Bearer "+token
    if body is not None:h["Content-Type"]="application/json"
    h.update(headers or {})
    data=json.dumps(body).encode() if body is not None else None
    try:
        with urlopen(Request(BASE+path,headers=h,method=method,data=data),timeout=8) as f:
            data=f.read()
            return f.status, json.loads(data) if f.headers.get("Content-Type","").startswith("application/json") and data else {}
    except HTTPError as e:return e.code,{}

def main():
    assert_isolated()
    view=(ROOT/"secrets/view_token").read_text().strip()
    admin=(ROOT/"secrets/admin_token").read_text().strip()
    assert len(view)>40 and len(admin)>40 and view!=admin
    assert req("/")[0]==200
    assert req("/healthz")[0]==200
    endpoints=("/api/status","/api/fills","/api/candles","/api/decisions","/api/news","/api/audit")
    for e in endpoints:
        if e in ("/api/status","/api/fills"):
            assert req(e)[0]==401,("unauthorized access",e)
        assert req(e,view)[0]==200,("viewer denied read",e)
    print("UNAUTHENTICATED_GET_REJECTED_AND_VIEWER_GET_PASS")
    assert req("/api/control/pause",view,"POST",{"paused":True})[0]==403
    assert req("/api/control/pause",admin,"POST",{"paused":"yes"})[0]==422
    assert req("/api/control/pause",admin,"POST",{"paused":True,"surprise":1})[0]==422
    assert req("/api/control/pause",admin,"POST",{"paused":True},headers={"Origin":"https://attacker.example"})[0]==403
    assert req("/api/status",admin,headers={"Host":"attacker.example"})[0] in (400,421)
    print("READONLY_AND_VALIDATION_AND_ORIGIN_HOST_PASS")
    status,start=req("/api/status",admin)
    assert status==200 and start["actor_role"]=="admin" and start["mode"]=="DRY_RUN_ONLY"
    assert start["real_coincheck_assets"]=="unconnected"
    assert float(start["effective_buy_budget_jpy"]) in (50000,13000)
    assert {w["name"] for w in start["portfolios"]}=={"baseline","news_gate"}
    assert start["runtime"]["strategy_version"].endswith("atr14-sl3-tp12")
    assert start["runtime"]["news_mode"]=="mock"
    assert start["news"]["state"]=="unknown"
    before=bool(start["pause_new_buys"])
    try:
        code,done=req("/api/control/pause",admin,"POST",
                      {"paused":not before,"reason":"isolated_http_e2e"})
        assert code==200 and done["paused"] is not before and done.get("request_id")
        assert req("/api/status",admin)[1]["pause_new_buys"] is not before
        audit=req("/api/audit",admin)[1]
        assert any(x["request_id"]==done["request_id"] and x["result"]=="success" for x in audit)
        print("AUDITED_PAUSE_POST_ATOMIC_AND_READBACK_PASS")
    finally:
        code,restored=req("/api/control/pause",admin,"POST",
                          {"paused":before,"reason":"restore_pretest_control"})
        assert code==200 and restored["paused"] is before
    print("RESTORED_ORIGINAL_PAUSE_STATE",before)
    print("API_SMOKE_SUCCESS_POST_RESTORED")
if __name__=="__main__":main()
