import os
import json
import unittest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import patch
from pathlib import Path

from app.config import Settings
from app.strategy import Candle, ema, rsi, atr, contiguous, evaluate
from app.market import parse_coincheck_trades
from app.paper import buy_from_asks, sell_to_bids, bracket
from app.store import bar_start, ceil_bar
from app.news import validate_news

UTC=timezone.utc

class ConfigTests(unittest.TestCase):
    def test_reject_live(self):
        with patch.dict(os.environ,{'DATABASE_URL':'postgresql://test','DRY_RUN':'false'},clear=True):
            with self.assertRaises(RuntimeError):Settings.load()
        with patch.dict(os.environ,{'DATABASE_URL':'postgresql://test','LIVE_TRADING_ENABLED':'true'},clear=True):
            with self.assertRaises(RuntimeError):Settings.load()

    def test_reject_private_exchange_keys(self):
        with patch.dict(os.environ,{'DATABASE_URL':'postgresql://test','COINCHECK_API_KEY':'test'},clear=True):
            with self.assertRaises(RuntimeError):Settings.load()

    def test_budget_cap(self):
        with patch.dict(os.environ,{'DATABASE_URL':'postgresql://test','BUY_BUDGET_JPY':'50001'},clear=True):
            with self.assertRaises(RuntimeError):Settings.load()
        with patch.dict(os.environ,{'DATABASE_URL':'postgresql://test','BUY_BUDGET_JPY':'50000'},clear=True):
            self.assertEqual(Settings.load().buy_jpy,Decimal('50000'))

class MarketTests(unittest.TestCase):
    def test_websocket_batch(self):
        payload=json.dumps([['1762000000','456','btc_jpy','12000000','0.002','buy','1','2',None]])
        ticks=parse_coincheck_trades(payload)
        self.assertEqual(len(ticks),1)
        self.assertEqual(ticks[0].trade_id,'456')
        self.assertEqual(ticks[0].price,Decimal('12000000'))

    def test_ignore_orderbook_and_bad_price(self):
        self.assertEqual(parse_coincheck_trades('["btc_jpy",{"asks":[]}]'),[])
        self.assertEqual(parse_coincheck_trades(json.dumps([['1762000000','5','btc_jpy','-5','1','buy']])),[])
        self.assertEqual(parse_coincheck_trades(json.dumps([['1762000000','5','eth_jpy','120','1','buy']])),[])

    def test_bucket(self):
        dt=datetime(2026,1,1,3,10,tzinfo=UTC)
        self.assertEqual(bar_start(dt,120),datetime(2026,1,1,2,tzinfo=UTC))
        self.assertEqual(ceil_bar(dt,240),datetime(2026,1,1,4,tzinfo=UTC))

class PaperTests(unittest.TestCase):
    def test_buy_fills_and_cap(self):
        asks=[(Decimal('12000000'),Decimal('1'))]
        fill=buy_from_asks(asks,Decimal('50000'))
        self.assertIsNotNone(fill)
        self.assertGreaterEqual(fill.qty,Decimal('0.001'))
        self.assertLessEqual(fill.jpy,Decimal('50000'))
        with self.assertRaises(ValueError):buy_from_asks(asks,Decimal('50001'))

    def test_sell_all(self):
        fill=sell_to_bids([(Decimal('12000000'),Decimal('0.01'))],Decimal('0.002'))
        self.assertEqual(fill.qty,Decimal('0.002'))
        self.assertEqual(fill.jpy,Decimal('24000'))
        self.assertIsNone(sell_to_bids([(Decimal('12000000'),Decimal('0.0001'))],Decimal('0.002')))

    def test_minimum_check(self):
        self.assertIsNone(buy_from_asks([(Decimal('19000000'),Decimal('1'))],Decimal('5000')))
        self.assertIsNone(sell_to_bids([(Decimal('12000000'),Decimal('1'))],Decimal('0.0001')))

    def test_bracket(self):
        stop,target=bracket(Decimal('12000000'),Decimal('50000'))
        self.assertEqual(stop,Decimal('11850000'))
        self.assertEqual(target,Decimal('12600000'))
        with self.assertRaises(ValueError):bracket(Decimal('10000'),Decimal('20000'))

class SignalTests(unittest.TestCase):
    def test_indicator_lengths(self):
        vals=list(range(1,301))
        self.assertEqual(len(ema(vals,55)),300)
        self.assertEqual(len(rsi(vals,9)),300)
        self.assertIsNone(rsi(vals,9)[7])

    def test_history_required(self):
        now=datetime(2026,1,1,tzinfo=UTC)
        bars=[Candle(now+timedelta(hours=i*2),100,101,99,100,1) for i in range(100)]
        sig=evaluate(bars,[])
        self.assertFalse(sig.valid)
        self.assertEqual(sig.reason,'warmup_incomplete')

    def test_history_gap_rejected(self):
        t=datetime(2026,1,1,tzinfo=UTC)
        bars=[Candle(t+timedelta(hours=i*2+(1 if i>=8 else 0)),100,101,99,100,1) for i in range(200)]
        h=[Candle(t+timedelta(hours=i*4),100,101,99,100,1) for i in range(300)]
        self.assertEqual(evaluate(bars,h).reason,'history_gap')

    def test_atr_const(self):
        t=datetime(2026,1,1,tzinfo=UTC)
        bars=[Candle(t+timedelta(hours=i*2),100,105,95,100,1) for i in range(30)]
        self.assertAlmostEqual(atr(bars,14)[-1],10)

class NewsTests(unittest.TestCase):
    def test_unverified_source_fail_closed(self):
        assessment=validate_news({'state':'clear','summary':'OK','valid_for_minutes':30,
                       'sources':[{'title':'Rumor','url':'http://example.com'}]},'mock',{})
        self.assertEqual(assessment.state,'unknown')

    def test_official_domain_alone_cannot_clear(self):
        assessment=validate_news({'state':'clear','summary':'Sample','valid_for_minutes':30,
                       'sources':[{'title':'SEC','url':'https://www.sec.gov/example'}]},'test',{})
        self.assertEqual(assessment.state,'unknown')

    def test_expiry_rejected(self):
        a=validate_news({'state':'clear','summary':'Sample','valid_for_minutes':500,
                         'sources':[{'title':'SEC','url':'https://www.sec.gov/example'}]},'test',{})
        self.assertEqual(a.state,'unknown')

class RepositoryTests(unittest.TestCase):
    def test_no_actual_order_adapter_in_application(self):
        root=Path(__file__).resolve().parents[1]
        code='\n'.join(p.read_text() for p in (root/'app').glob('*.py'))
        self.assertNotIn('create_order(',code)
        self.assertNotIn('market_buy_amount',code)
        self.assertNotIn('api.coincheck.com/api/exchange/orders',code)

if __name__=='__main__':unittest.main()
