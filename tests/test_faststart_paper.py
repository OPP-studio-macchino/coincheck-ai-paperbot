"""Offline tests for the isolated FAST_START_PROXY (no network calls)."""
from __future__ import annotations
import hashlib, json, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.faststart_paper import (FastStartError, SIGNAL_SOURCE, EXECUTION_SOURCE,
    aggregate, load_bootstrap, run_once)
from app.strategy import Candle, Signal

UTC=timezone.utc

def row(at, price=10_000_000, volume="1"):
    if volume=="0":
        return [str(price)]*4+[volume,int(at.timestamp()*1000)]
    return [str(price),str(price+100),str(price-100),str(price+10),volume,int(at.timestamp()*1000)]

def bootstrap(root: Path, count=1296, start=datetime(2026,1,1,tzinfo=UTC), zero=False):
    entries=[]
    for first in range(0,count,24):
        rows=[row(start+timedelta(hours=i),volume="0" if zero and i==9 else "1") for i in range(first,min(first+24,count))]
        date=(start+timedelta(hours=first)).strftime("%Y%m%d")
        raw=json.dumps({"success":1,"data":{"candlestick":[{"type":"1hour","ohlcv":rows}]}}).encode()
        (root/(date+".json")).write_bytes(raw)
        entries.append({"date":date,"sha256":hashlib.sha256(raw).hexdigest(),"rows":len(rows)})
    (root/"manifest.json").write_text(json.dumps({"source":"public.bitbank.cc","pair":"btc_jpy","tf":"1hour","entries":entries}))
    return start+timedelta(hours=count,minutes=3)

def fake_fetch(url):
    t=int((datetime(2026,1,1,tzinfo=UTC)+timedelta(hours=1296,minutes=3)).timestamp())
    if "bitbank.cc/btc_jpy/ticker" in url:
        return {"data":{"last":"10000000","timestamp":t*1000}}
    if "coincheck.com/api/ticker" in url:
        return {"last":"10000000","timestamp":t}
    if "order_books" in url: return {"asks":[["10000000","1"]],"bids":[["10000000","1"]]}
    raise AssertionError(url)

class FastStartTests(unittest.TestCase):
    def test_tampered_manifest_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); bootstrap(root)
            target=next(root.glob("2026*.json")); target.write_text("{}")
            with self.assertRaisesRegex(FastStartError,"SHA"): load_bootstrap(root)

    def test_zero_volume_is_risk_marked_and_missing_is_not_fabricated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); now=bootstrap(root,zero=True); bars,flags=load_bootstrap(root)
            self.assertTrue(any(x.startswith("ZERO_VOLUME_PUBLISHED") for x in flags))
            self.assertEqual(len(aggregate(bars,2,now)),648); self.assertEqual(len(aggregate(bars,4,now)),324)
            manifest=json.loads((root/"manifest.json").read_text()); manifest["entries"][2]["rows"]-=1
            (root/"manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(FastStartError): load_bootstrap(root)

    def test_live_false_does_not_buy_and_labels_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); state=root/"private"; now=bootstrap(root)
            result=run_once(root,state,now=now,fetch=fake_fetch)
            self.assertEqual(result["position_btc"],"0"); self.assertEqual(result["signal_source"],SIGNAL_SOURCE)
            self.assertTrue(result["history"]["ready"])
            self.assertFalse(result["signal"]["valid"])
            self.assertEqual(result["cross_venue_basis_pct"],"0.0000")
            self.assertEqual(json.loads((state/"state.json").read_text())["execution_source"],EXECUTION_SOURCE)

    def test_valid_signal_only_fills_once_and_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); state=root/"private"; now=bootstrap(root)
            sig=lambda a,b: Signal(True,"test",1000,{"bar_start_utc":(now-timedelta(hours=2,minutes=5)).isoformat()})
            one=run_once(root,state,now=now,fetch=fake_fetch,signal_fn=sig)
            self.assertNotEqual(one["position_btc"],"0"); self.assertGreaterEqual(float(one["cash_jpy"]),50000)
            two=run_once(root,state,now=now,fetch=fake_fetch,signal_fn=sig)
            self.assertEqual(two["position_btc"],one["position_btc"])
            self.assertEqual(sum(1 for x in (state/"events.jsonl").read_text().splitlines() if "PAPER_BUY" in x),1)

    def test_stale_and_basis_gap_suppress_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); state=root/"private"; now=bootstrap(root)
            stale=lambda a,b: Signal(True,"test",1000,{"bar_start_utc":(now-timedelta(hours=2,minutes=11)).isoformat()})
            self.assertEqual(run_once(root,state,now=now,fetch=fake_fetch,signal_fn=stale)["position_btc"],"0")
            fresh=lambda a,b: Signal(True,"test",1000,{"bar_start_utc":(now-timedelta(hours=2,minutes=5)).isoformat()})
            def gap(url):
                value=fake_fetch(url)
                return {**value,"last":"10200000"} if "coincheck.com/api/ticker" in url else value
            self.assertEqual(run_once(root,state,now=now,fetch=gap,signal_fn=fresh)["position_btc"],"0")

    def test_public_ticker_timestamp_required_and_stale_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); state=root/"private"; now=bootstrap(root)
            signal=lambda a,b: Signal(True,"test",1000,{"bar_start_utc":(now-timedelta(hours=2,minutes=5)).isoformat()})
            def missing_timestamp(url):
                x=fake_fetch(url)
                if "coincheck.com/api/ticker" in url:
                    x.pop("timestamp",None)
                return x
            result=run_once(root,state,now=now,fetch=missing_timestamp,signal_fn=signal)
            self.assertEqual(result["position_btc"],"0")
            self.assertEqual(result["last_error"],"TICKER_MALFORMED")

    def test_atomic_snapshot_contains_ledger_and_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); state=root/"private"; now=bootstrap(root)
            signal=lambda a,b: Signal(True,"test",1000,{"bar_start_utc":(now-timedelta(hours=2,minutes=5)).isoformat()})
            run_once(root,state,now=now,fetch=fake_fetch,signal_fn=signal)
            saved=json.loads((state/"state.json").read_text())
            self.assertEqual(saved["position_btc"]!="0",True)
            self.assertEqual(len(saved["events"]),1)
            self.assertEqual(saved["events"][0]["kind"],"PAPER_BUY")
            (state/"events.jsonl").unlink()
            run_once(root,state,now=now,fetch=fake_fetch,signal_fn=signal)
            self.assertEqual(len(json.loads((state/"state.json").read_text())["events"]),1)
            self.assertEqual(len((state/"events.jsonl").read_text().splitlines()),1)

    def test_exit_uses_coincheck_bid_without_new_buy_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); state=root/"private"; now=bootstrap(root)
            (state).mkdir(); (state/"state.json").write_text(json.dumps({"version":1,"mode":"PAPER_ONLY","signal_source":SIGNAL_SOURCE,"execution_source":EXECUTION_SOURCE,"fee_bps":"0","cash_jpy":"50000","position_btc":"0.00400000","entry":"10000000","stop":"9900000","take_profit":"10100000","realized_jpy":"0","last_bar":None,"last_filled_bar":None,"last_quote_at":None,"last_error":None,"next_event_id":1}))
            def exit_book(url):
                x=fake_fetch(url)
                return {"asks":[["10200000","1"]],"bids":[["10200000","1"]]} if "order_books" in url else x
            result=run_once(root,state,now=now,fetch=exit_book)
            self.assertEqual(result["position_btc"],"0")

if __name__=="__main__": unittest.main()
