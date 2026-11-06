import copy
import json
from pathlib import Path
import tempfile
import unittest

from ai_lab.contracts import BASELINE, LabError, POLICY, strict_json, validate_candidate, validate_environment
from ai_lab.ledger import Ledger


class CandidateBoundaryTests(unittest.TestCase):
    def test_baseline_identity_and_research_atr_boundary(self):
        from app import config
        self.assertEqual((BASELINE.ema_fast, BASELINE.ema_slow, BASELINE.rsi_period,
                          BASELINE.rsi_threshold, BASELINE.rsi_window,
                          BASELINE.atr_period, BASELINE.stop_atr, BASELINE.target_atr),
                         (config.EMA_FAST, config.EMA_SLOW, config.RSI_PERIOD,
                          config.RSI_THRESHOLD, config.RSI_WINDOW,
                          config.ATR_PERIOD, float(config.STOP_ATR), float(config.TARGET_ATR)))
        changed = BASELINE.to_dict()
        changed["stop_atr"] = 2
        self.assertEqual(validate_candidate(changed).stop_atr, 2)
        self.assertEqual(float(config.STOP_ATR), 3)

    def test_forbidden_capabilities_and_types(self):
        for key, value in (("buy_budget_jpy",100000), ("python","print(1)"),
                           ("url","https://example.invalid"), ("deploy",True)):
            candidate = BASELINE.to_dict()
            candidate[key] = value
            with self.assertRaises(LabError): validate_candidate(candidate)
        for key, value in (("ema_fast",True), ("stop_atr",float("nan")),
                           ("ema_fast",56), ("rsi_window",-1), ("stop_atr",0),
                           ("name","../secrets"), ("entry_filters",["rsi_cross"]),
                           ("entry_filters",["local_trend","higher_trend","rsi_cross","python"])):
            candidate = BASELINE.to_dict()
            candidate[key] = value
            with self.assertRaises(LabError): validate_candidate(candidate)

    def test_semantic_duplicate_renaming_and_filter_order(self):
        value = BASELINE.to_dict()
        value["name"] = "renamed"
        value["entry_filters"].reverse()
        value["volatility_cap_pct"] = 1
        self.assertEqual(BASELINE.strategy_hash, validate_candidate(value).strategy_hash)

    def test_json_boundary(self):
        for bad in ('{"x":1,"x":2}', '{"x": NaN}', '{"x": Infinity}', '['*10000,
                    '{"x":'+'9'*5000+'}'):
            with self.assertRaises(LabError): strict_json(bad)
        with self.assertRaises(LabError): strict_json(' '*20000)
        big=BASELINE.to_dict()
        big['stop_atr']=10**400
        with self.assertRaises(LabError): validate_candidate(big)

    def test_environment_fail_closed(self):
        validate_environment({})
        for key,value in (("DRY_RUN","false"),("LIVE_TRADING_ENABLED","true"),
                          ("NEWS_MODE","live"),("AI_LAB_PROVIDER","openai"),
                          ("OPENAI_API_KEY","fixture-only"),("COINCHECK_API_KEY","fixture-only"),
                          ("DATABASE_URL","fixture-only")):
            with self.assertRaises(LabError): validate_environment({key:value})


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "research"
        self.ledger = Ledger(self.root)
        self.manifest = {"max_trials":2,"data_hash":"one","policy":"one"}
        self.ledger.begin_run("test-run",self.manifest)

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def test_reentry_dedup_and_resume_identity(self):
        self.assertTrue(self.ledger.reserve_trial("test-run",0,2))
        self.assertFalse(self.ledger.reserve_trial("test-run",0,2))
        self.ledger.finish_trial("test-run",0,"FAILED",error_code="INTERRUPTED")
        self.assertEqual(len(self.ledger.trials("test-run")),1)
        self.assertTrue(self.ledger.reserve_trial("test-run",1,2))
        with self.assertRaises(LabError): self.ledger.reserve_trial("test-run",2,3)
        with self.assertRaises(LabError): self.ledger.begin_run("test-run",{"max_trials":2,"data_hash":"two"})
        self.assertTrue(self.ledger.verify_events())

    def test_audit_transaction_failure_rolls_back(self):
        before = len(self.ledger.trials("test-run"))
        real = self.ledger._event
        def fail(*args): raise OSError("fixture disk full")
        self.ledger._event = fail
        with self.assertRaises(OSError): self.ledger.reserve_trial("test-run",0,2)
        self.ledger._event = real
        self.assertEqual(len(self.ledger.trials("test-run")),before)

    def test_concurrent_writer_and_persistent_state(self):
        other = Ledger(self.root)
        try:
            with self.ledger.single_writer():
                with self.assertRaises(LabError):
                    with other.single_writer(): pass
                self.ledger.reserve_trial("test-run",0,2)
            self.assertEqual(other.trials("test-run")[0]["status"],"RESERVED")
        finally: other.close()

    def test_holdout_one_use_and_overlapping_snapshot(self):
        self.ledger.reserve_final("test-run","a"*64,"b"*64,100,200)
        with self.assertRaises(LabError): self.ledger.reserve_final("test-run","a"*64,"b"*64,100,200)
        self.ledger.finish_final("test-run","a"*64,{"pass":False})
        self.assertEqual(self.ledger.reserve_final("test-run","a"*64,"b"*64,100,200),{"pass":False})
        with self.assertRaises(LabError): self.ledger.reserve_final("test-run","c"*64,"b"*64,150,250)
        with self.assertRaises(LabError): self.ledger.reserve_final("test-run","a"*64,"b"*64,100,201)
        self.ledger.begin_run("another",self.manifest)
        with self.assertRaises(LabError): self.ledger.reserve_final("another","c"*64,"d"*64,150,250)
        self.ledger.reserve_final("another","e"*64,"f"*64,200,300)

    def test_existing_directory_and_symlink_rejected(self):
        unrelated = Path(self.tmp.name)/"bot"
        unrelated.mkdir()
        (unrelated/"important.txt").write_text("preserve")
        with self.assertRaises(LabError): Ledger(unrelated)
        link = Path(self.tmp.name)/"link"
        link.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(LabError): Ledger(link)
        self.assertEqual((unrelated/"important.txt").read_text(),"preserve")

    def test_reserved_holdout_remains_visible_after_interruption(self):
        from ai_lab.report import write_report
        self.ledger.reserve_final("test-run","a"*64,"b"*64,100,200)
        snapshot=self.ledger.snapshot("test-run")
        self.assertEqual(snapshot["final_tests"][0]["status"],"RESERVED")
        output=self.root/"report.html"
        write_report(snapshot,output)
        self.assertIn("予約済み・結果未確定",output.read_text())
        self.assertIn("実験は未完了",output.read_text())
        self.assertNotIn("研究候補の比較が完了しました",output.read_text())


if __name__ == "__main__": unittest.main()
