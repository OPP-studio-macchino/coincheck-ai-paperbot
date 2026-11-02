"""Independent periodic news job; no exchange credentials or order access."""
import logging,os,time
from datetime import datetime,timezone,timedelta
from .config import Settings
from .store import Store
from .news import assess_live,assess_mock

logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')


def main():
    cfg=Settings.load()
    while True:
        try:
            db=Store()
            try:
                if cfg.news_mode=='live' and db.daily_news_calls() >= 48:
                    out=assess_mock()
                    db.record_news('unknown','OpenAI daily call limit reached',[],
                        datetime.now(timezone.utc)+timedelta(minutes=30),'budget_exceeded',{})
                else:
                    out=assess_live() if cfg.news_mode=='live' else assess_mock()
                    db.record_news(out.state,out.summary,out.sources,out.expires_at,out.model,out.usage)
                    logging.info('NEWS state=%s sources=%s mode=%s',out.state,len(out.sources),cfg.news_mode)
            finally:db.close()
        except Exception as exc:
            logging.error('NEWS assessment failed type=%s',type(exc).__name__)
            try:
                store=Store()
                store.record_news('unknown','News analysis unavailable',[],
                    datetime.now(timezone.utc)+timedelta(minutes=5),'api_failed',{})
                store.close()
            except Exception:logging.error('News fail-closed record unavailable')
        time.sleep(1800)

if __name__=='__main__':main()
