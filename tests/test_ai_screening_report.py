from pathlib import Path
import tempfile
import unittest

from ai_lab.contracts import LabError
from ai_lab.report import write_report
from ai_lab.screening import screen


def metrics(profit=1000, trades=25, dd=4, share=.25):
    return {"net_profit_jpy":profit,"closed_trades":trades,
            "max_drawdown_pct":dd,"max_single_trade_profit_share":share}


class ScreeningTests(unittest.TestCase):
    def test_profit_alone_cannot_pass(self):
        reasons = screen(metrics(100000,trades=2,dd=20,share=.9),metrics(0),metrics(0),metrics(0),
                         [metrics(-1),metrics(1),metrics(-1)])
        self.assertEqual(set(reasons),{"INSUFFICIENT_CLOSED_TRADES","DRAWDOWN_LIMIT",
                                      "PROFIT_CONCENTRATION","COST_STRESS_FAILED","DEV_FOLD_STABILITY"})

    def test_equal_baseline_does_not_qualify(self):
        self.assertIn("NO_BASELINE_IMPROVEMENT",screen(metrics(),metrics(),metrics(),metrics(0)))

    def test_fixed_thresholds_and_missing_results(self):
        self.assertEqual(screen(metrics(),metrics(500),metrics(700),metrics(300),
                                [metrics(1),metrics(-1),metrics(1)]),[])
        with self.assertRaises(LabError): screen({},metrics(),metrics(),metrics())
        bad = metrics()
        bad["max_drawdown_pct"] = float("nan")
        with self.assertRaises(LabError): screen(bad,metrics(),metrics(),metrics())

    def test_report_escapes_untrusted_strings_and_marks_synthetic(self):
        with tempfile.TemporaryDirectory() as directory:
            dest=Path(directory)/"report.html"
            write_report({"run":{"run_id":"fixture","started_at":"2026-11-05T00:00:00Z",
                                   "manifest":{"max_trials":1,"buy_budget_jpy":13000,"initial_cash_jpy":42000,
                                               "provenance":{"source":"synthetic"}},
                                   "result":{"outcome":"DEMO_ONLY"}},
                          "trials":[{"trial_index":0,"status":"REJECTED",
                                     "candidate":{"name":"<script>alert(1)</script>"},"result":{}}],
                          "event_chain_ok":True},dest)
            content=dest.read_text()
            self.assertNotIn("<script>",content)
            self.assertIn("&lt;script&gt;",content)
            self.assertIn("合成価格",content)
            self.assertIn("13,000",content)
            self.assertIn("42,000",content)
            self.assertNotIn("100,000",content)
            self.assertIn("default-src 'none'",content)


if __name__ == "__main__": unittest.main()
