"""Separate paper-only news loop; durable attempt reservation before paid API."""
from __future__ import annotations
import logging,os,time
from datetime import datetime,timezone,timedelta
from .config import Settings
from .store import Store
from .news import assess_mock,assess_live

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")

def once(cfg:Settings):
    db=Store("news")
    attempt_id=None
    try:
        if cfg.news_mode=="mock":
            out=assess_mock()
            db.record_news(out.state,out.summary,out.sources,out.expires_at,out.model,out.usage)
            return out.state
        # Atomic quota check + INSERT committed before calling the paid API.
        attempt_id=db.reserve_news_attempt(limit=24)
        if attempt_id is None:
            out=assess_mock()
            db.record_news("unknown","Daily API attempt cap reached",[],
                datetime.now(timezone.utc)+timedelta(minutes=5),"quota",{})
            return "unknown"
        try:
            out=assess_live()
            db.settle_news_attempt(attempt_id,success=True)
            db.record_news(out.state,out.summary,out.sources,out.expires_at,out.model,out.usage)
            return out.state
        except Exception:
            db.settle_news_attempt(attempt_id,success=False)
            db.record_news("unknown","News request failure",[],
                datetime.now(timezone.utc)+timedelta(minutes=5),"request_failed",{})
            return "unknown"
    finally:db.close()

def main():
    cfg=Settings.load()
    every=int(os.environ.get("NEWS_INTERVAL_SECONDS","1800"))
    if not 60<=every<=7200:raise RuntimeError("Invalid news interval")
    while True:
        try:
            state=once(cfg)
            logging.info("NEWS mode=%s state=%s",cfg.news_mode,state)
        except Exception as e:
            logging.error("NEWS_UNAVAILABLE category=%s",type(e).__name__)
            # No fallback to clear; market's time expiry blocks new news_gate buys.
        time.sleep(every)

if __name__=="__main__":main()
