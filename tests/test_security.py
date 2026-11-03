"""Pure-Python fail-closed regression tests. No network/API/real orders."""
from __future__ import annotations
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
import os
import tempfile
import unittest
from app.config import (Settings,VERSION,STRATEGY_VERSION,MAX_BUY_JPY,
                        EMA_FAST,EMA_SLOW,RSI_PERIOD,RSI_THRESHOLD,RSI_WINDOW,
                        UPPER_EMA_FAST,UPPER_EMA_SLOW,ATR_PERIOD,STOP_ATR,
                        TARGET_ATR,LOCAL_REQUIRED,UPPER_REQUIRED,read_secret)
from app.paper import buy_from_asks,sell_to_bids,bracket
from app.news import validate_news,assess_mock,_safe_https_url
from app.market_worker import current_news
from app.store import trailing_contiguous,bar_start
from app.strategy import Candle

class SettingsSecurityTests(unittest.TestCase):
    def _load(self,extra=None):
        env={"DRY_RUN":"true","LIVE_TRADING_ENABLED":"false","NEWS_MODE":"mock",
             "BUY_BUDGET_JPY":"50000","INITIAL_CASH_JPY":"100000"}
        env.update(extra or {})
        with patch.dict(os.environ,env,clear=True):return Settings.load()

    def test_strategy_contract_unchanged(self):
        self.assertEqual((EMA_FAST,EMA_SLOW,RSI_PERIOD,RSI_THRESHOLD,RSI_WINDOW),(21,55,9,55,3))
        self.assertEqual((UPPER_EMA_FAST,UPPER_EMA_SLOW),(50,200))
        self.assertEqual((ATR_PERIOD,STOP_ATR,TARGET_ATR),(14,Decimal(3),Decimal(12)))
        self.assertEqual((LOCAL_REQUIRED,UPPER_REQUIRED),(200,300))
        self.assertIn("btc2h-",STRATEGY_VERSION)

    def test_no_real_orders_or_private_keys(self):
        for extra in ({"DRY_RUN":"false"},
                      {"LIVE_TRADING_ENABLED":"true"},
                      {"COINCHECK_API_KEY":"abc"},
                      {"COINCHECK_API_SECRET":"abc"},
                      {"COINCHECK_ACCESS_KEY":"abc"},
                      {"NEWS_MODE":"live"}):
            with self.subTest(extra=next(iter(extra))):
                with self.assertRaises(RuntimeError):self._load(extra)

    def test_invalid_budget_and_fee(self):
        for value in ("0","-1","50001","NaN","Infinity","13000.3","1e100","abc"):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError):self._load({"BUY_BUDGET_JPY":value})
        for value in ("-1","101","NaN"):
            with self.subTest(fee=value):
                with self.assertRaises(RuntimeError):self._load({"PAPER_FEE_BPS":value})

    def test_50000_vs_13000_effective_budget(self):
        self.assertEqual(self._load().buy_jpy,Decimal("50000"))
        self.assertEqual(self._load({"BUY_BUDGET_JPY":"13000"}).buy_jpy,Decimal("13000"))
        self.assertEqual(self._load({"BUY_BUDGET_JPY":"13000"}).initial_jpy,Decimal("100000"))

    def test_read_secret_file_never_uses_value_from_env(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"token"
            path.write_text("x"*54)
            with patch.dict(os.environ,{"ADMIN_TOKEN_FILE":str(path)}):
                self.assertEqual(read_secret("ADMIN_TOKEN_FILE"),"x"*54)
            with patch.dict(os.environ,{"ADMIN_TOKEN_FILE":"rawsecret"}):
                with self.assertRaises(RuntimeError):read_secret("ADMIN_TOKEN_FILE")

    def test_budget_contains_fees(self):
        asks=[(Decimal("8000000"),Decimal("1"))]
        for budget in (Decimal("50000"),Decimal("13000")):
            f=buy_from_asks(asks,budget,Decimal("25"))
            self.assertIsNotNone(f)
            self.assertLessEqual(f.total_buy_jpy,budget)
            self.assertGreaterEqual(f.qty,Decimal("0.001"))
        self.assertLessEqual(buy_from_asks(asks,Decimal("13000")).jpy,Decimal("13000"))
        self.assertIsNone(buy_from_asks([(Decimal("20000000"),Decimal("1"))],Decimal("13000")))

    def test_insufficient_depth_or_cash(self):
        self.assertIsNone(buy_from_asks([(Decimal("10000000"),Decimal("0.0005"))],Decimal("50000")))
        self.assertIsNone(sell_to_bids([(Decimal("10000000"),Decimal("0.00001"))],Decimal("0.002")))
        with self.assertRaises(ValueError):buy_from_asks([],Decimal("NaN"))
        with self.assertRaises(ValueError):buy_from_asks([],Decimal("50001"))

    def test_brackets_fixed_after_entry(self):
        self.assertEqual(bracket(Decimal("8000000"),Decimal("100000")),
                         (Decimal("7700000"),Decimal("9200000")))

class NewsBoundaryTests(unittest.TestCase):
    def test_never_clear_from_domain_only(self):
        a=validate_news({"state":"clear","summary":"buy","valid_for_minutes":30,
          "sources":[{"title":"SEC","url":"https://www.sec.gov/news"}]},"test",{})
        self.assertEqual(a.state,"unknown")
        self.assertEqual(a.sources,[])

    def test_verified_evidence_requires_all_metadata(self):
        now=datetime.now(timezone.utc)
        url="https://www.sec.gov/news"
        base={"state":"clear","summary":"published","valid_for_minutes":30,
              "sources":[{"title":"SEC","url":url}]}
        receipt={"url":url,"http_status":200,"content_sha256":"a"*64,
                 "published_at_utc":(now-timedelta(minutes=5)).isoformat(),
                 "fetched_at_utc":now.isoformat(),"factual_match":True}
        self.assertEqual(validate_news(base,"fixture",{},now,[receipt]).state,"clear")
        self.assertEqual(validate_news(base,"fixture",{},now,[{**receipt,"factual_match":False}]).state,"unknown")
        self.assertEqual(validate_news(base,"fixture",{},now,[{**receipt,"published_at_utc":(now-timedelta(days=3)).isoformat()}]).state,"unknown")
        self.assertEqual(validate_news(base,"fixture",{},now,[{**receipt,"http_status":404}]).state,"unknown")

    def test_mock_cannot_clear_or_use_paid_api(self):
        a=assess_mock()
        self.assertEqual(a.state,"unknown")
        self.assertEqual(a.model,"mock")

    def test_news_gate_never_clears_in_mock_even_if_db_row_claims_clear(self):
        class DB:
            def latest_news(self):
                now=datetime.now(timezone.utc)
                return ("clear","forged",now+timedelta(minutes=25),[{"url":"https://www.sec.gov/x"}],now)
        self.assertEqual(current_news(DB(),datetime.now(timezone.utc),"mock")[0],"unknown")
        self.assertEqual(current_news(DB(),datetime.now(timezone.utc),"live")[0],"unknown")

    def test_news_urls_no_internal_or_query_secrets(self):
        for url in ("https://127.0.0.1/x","https://10.0.0.1/x",
                    "https://metadata.google.internal/x","https://example.com/path?token=abc",
                    "http://www.sec.gov/x","https://localhost/x"):
            with self.subTest(url=url):
                self.assertIsNone(_safe_https_url(url))
        self.assertEqual(_safe_https_url("https://www.sec.gov/news"),
                         "https://www.sec.gov/news")

class HistoryTests(unittest.TestCase):
    def test_consecutive_only_confirmed_spacing(self):
        at=datetime(2026,1,1,tzinfo=timezone.utc)
        a=[Candle(at+timedelta(hours=2*i),8,9,7,8,1) for i in range(5)]
        self.assertEqual(trailing_contiguous(a,120),5)
        a[2]=Candle(at+timedelta(hours=99),8,9,7,8,1)
        self.assertEqual(trailing_contiguous(a,120),2)

if __name__=="__main__":unittest.main()
