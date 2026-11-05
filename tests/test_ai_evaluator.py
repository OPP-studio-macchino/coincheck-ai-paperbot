"""Causal, execution and input-contract checks for the offline evaluator."""
from __future__ import annotations

import csv
import io
import json
import math
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.strategy import Candle, evaluate as production_evaluate
from ai_lab.contracts import BASELINE, LabError, POLICY
from ai_lab.dataset import Dataset, load_csv, make_fixture, chronological_split
from ai_lab.evaluator import Decision, _aggregate, build_signals, evaluate, simulate

UTC = timezone.utc
START = datetime(2020, 1, 1, tzinfo=UTC)


def candle(index, opening=10_000_000.0, high=None, low=None, close=None):
    return Candle(START + timedelta(minutes=5 * index), opening,
                  opening if high is None else high,
                  opening if low is None else low,
                  opening if close is None else close, 1.0)


def dataset(rows):
    return Dataset(tuple(rows), "test-dataset-sha", "synthetic")


def decision(index, atr=100_000.0, cross="A"):
    return Decision(index, START + timedelta(minutes=5 * index), True,
                    atr, cross, {}, "signal")


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def write(self, rows, headers=None):
        path = self.root / "prices.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(headers or ["timestamp", "open", "high", "low", "close", "volume", "source"])
            writer.writerows(rows)
        return path

    def row(self, index=0):
        return [(START + timedelta(minutes=5 * index)).isoformat(), 10, 11, 9, 10, 1, "coincheck"]

    def test_source_is_never_verified_by_csv_claim(self):
        actual = load_csv(self.write([self.row(), self.row(1)]))
        self.assertEqual(actual.source, "coincheck")
        self.assertFalse(actual.provenance_verified)
        self.assertFalse(actual.metadata()["market_evidence"])
        self.assertTrue(actual.metadata()["source_is_self_declared"])

    def test_fixture_is_deterministic_and_marked_synthetic(self):
        first = make_fixture(self.root / "first.csv", days=2, seed=73)
        second = make_fixture(self.root / "second.csv", days=2, seed=73)
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.candles, second.candles)
        self.assertEqual(first.source, "synthetic")
        self.assertFalse(first.metadata()["market_evidence"])

    def test_rejects_unsafe_market_rows(self):
        cases = {
            "duplicate": [self.row(), self.row()],
            "gap": [self.row(), self.row(2)],
            "reverse": [self.row(1), self.row()],
            "nonfinite": [self.row()],
            "nonpositive": [self.row()],
            "bad_high": [self.row()],
            "bad_low": [self.row()],
            "negative_volume": [self.row()],
            "unaligned": [self.row()],
            "naive": [self.row()],
            "non_utc": [self.row()],
            "mixed_source": [self.row(), self.row(1)],
        }
        cases["nonfinite"][0][1] = "NaN"
        cases["nonpositive"][0][3] = 0
        cases["bad_high"][0][2] = 9
        cases["bad_low"][0][3] = 11
        cases["negative_volume"][0][5] = -1
        cases["unaligned"][0][0] = "2020-01-01T00:01:00Z"
        cases["naive"][0][0] = "2020-01-01T00:00:00"
        cases["non_utc"][0][0] = "2020-01-01T09:00:00+09:00"
        cases["mixed_source"][1][6] = "bitbank"
        for name, rows in cases.items():
            with self.subTest(name=name), self.assertRaises(LabError):
                load_csv(self.write(rows))

    def test_rejects_unclosed_bar(self):
        with self.assertRaises(LabError):
            load_csv(self.write([self.row()]), as_of=START + timedelta(minutes=4))

    def test_availability_cannot_precede_close(self):
        headers = ["timestamp", "open", "high", "low", "close", "volume", "source", "available_at"]
        with self.assertRaises(LabError):
            load_csv(self.write([self.row() + [START.isoformat()]], headers))

    def test_duplicate_and_unknown_headers_rejected(self):
        for headers in (["timestamp", "open", "high", "low", "close", "volume", "open"],
                        ["timestamp", "open", "high", "low", "close", "volume", "secret"]):
            with self.subTest(headers=headers), self.assertRaises(LabError):
                load_csv(self.write([self.row()], headers))

    def test_malformed_quoted_header_is_a_sanitized_error(self):
        path = self.root / "invalid.csv"
        path.write_text('"unclosed-header', encoding="utf-8")
        with self.assertRaisesRegex(LabError, "^csv_header_invalid$"):
            load_csv(path)

    def test_split_is_chronological_and_4h_aligned(self):
        actual = make_fixture(self.root / "split.csv", days=60)
        parts = chronological_split(actual)
        self.assertEqual(parts["warmup"][0], 0)
        self.assertEqual(parts["warmup"][1], 50 * 288)
        self.assertEqual(parts["train"][0], parts["warmup"][1])
        self.assertEqual(parts["development"][0], parts["train"][1])
        self.assertEqual(parts["holdout"][0], parts["development"][1])
        for key in ("train", "development", "holdout"):
            start, end = parts[key]
            self.assertLess(start, end)
            self.assertEqual(start % 48, 0)
            self.assertEqual(end % 48, 0)


class SignalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.data = make_fixture(Path(cls.directory.name) / "signals.csv", days=90, seed=47)
        cls.signals = build_signals(cls.data, BASELINE)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_every_production_indicator_and_decision_matches(self):
        local, local_ends = _aggregate(self.data.candles, 120)
        higher, higher_ends = _aggregate(self.data.candles, 240)
        observed = {item.fill_index: item for item in self.signals}
        keys = ("bar_start_utc", "rsi9", "ema21", "ema55", "higher_ema50",
                "higher_ema200", "atr14", "rsi_window_age", "trend_ok",
                "higher_ok", "rsi_window_ok")
        checked = 0
        for idx in range(600, len(local), 7):
            h = sum(end <= local_ends[idx] for end in higher_ends)
            expected = production_evaluate(local[max(0, idx - 449):idx + 1],
                                           higher[max(0, h - 450):h])
            actual = observed[local_ends[idx]]
            self.assertEqual(actual.valid, expected.valid)
            self.assertAlmostEqual(actual.atr, expected.atr, places=8)
            for key in keys:
                self.assertEqual(actual.snapshot[key], expected.snapshot[key], key)
            checked += 1
        self.assertGreater(checked, 10)

    def test_future_tail_cannot_change_prefix_signals(self):
        boundary = 55 * 288
        altered = tuple(self.data.candles[:boundary]) + tuple(
            replace(row, open=row.open * 2, high=row.high * 2, low=row.low * 2,
                    close=row.close * 2) for row in self.data.candles[boundary:])
        alternative = replace(self.data, candles=altered)
        first = build_signals(self.data, BASELINE, end_index=boundary)
        second = build_signals(alternative, BASELINE, end_index=boundary)
        self.assertEqual(first, second)

    def test_higher_bars_are_confirmed_and_signal_fills_next_5m(self):
        for item in self.signals:
            if item.fill_index < len(self.data.candles):
                self.assertEqual(item.signal_end, self.data.candles[item.fill_index].start)
            end = item.snapshot.get("higher_bar_end_utc")
            if end:
                self.assertLessEqual(datetime.fromisoformat(end), item.signal_end)

    def test_delayed_availability_blocks_signal(self):
        availability = tuple(row.start + timedelta(minutes=5) for row in self.data.candles)
        delayed = list(availability)
        delayed[50 * 288] += timedelta(days=1)
        altered = replace(self.data, available_at=tuple(delayed))
        signals = build_signals(altered, BASELINE)
        window = [item for item in signals if 50 * 288 < item.fill_index < 51 * 288]
        self.assertTrue(window)
        self.assertTrue(all(not item.valid for item in window))
        self.assertTrue(all(item.reason == "data_unavailable_at_signal" for item in window))


class ExecutionTests(unittest.TestCase):
    def run_sim(self, rows, choices=None, multiplier=0):
        return simulate(dataset(rows), BASELINE, choices or [decision(1)],
                        start_index=1, end_index=len(rows), cost_multiplier=multiplier)

    def test_entry_uses_next_open_and_frozen_entry_atr(self):
        rows = [candle(0, 8_000_000), candle(1),
                candle(2, high=10_100_000, low=9_600_000)]
        result = self.run_sim(rows, [decision(1), decision(2, atr=1_000_000, cross="B")])
        trade = result.trades[0]
        self.assertEqual(trade["entry_price_jpy"], 10_000_000)
        self.assertEqual(trade["entry_atr"], 100_000)
        self.assertEqual(trade["stop_price_jpy"], 9_700_000)
        self.assertEqual(trade["exit_price_jpy"], 9_700_000)
        self.assertEqual(result.metrics.closed_trades, 1)

    def test_both_hit_uses_stop_first(self):
        rows = [candle(0), candle(1, high=12_000_000, low=9_000_000)]
        result = self.run_sim(rows)
        self.assertEqual(result.trades[0]["exit_price_jpy"], 9_700_000)
        self.assertEqual(result.metrics.ambiguous_exit_bars, 1)
        self.assertLess(result.metrics.net_profit_jpy, 0)

    def test_stop_gap_uses_worse_open(self):
        rows = [candle(0), candle(1), candle(2, 9_000_000, high=9_500_000, low=8_900_000)]
        result = self.run_sim(rows)
        self.assertEqual(result.trades[0]["exit_price_jpy"], 9_000_000)
        self.assertEqual(result.trades[0]["exit_reason"], "gap_stop")

    def test_cross_window_cannot_reenter_same_event(self):
        rows = [candle(0), candle(1, high=12_000_000), candle(2), candle(3, high=12_000_000)]
        same = self.run_sim(rows, [decision(1), decision(2), decision(3, cross="B")])
        self.assertEqual(same.metrics.closed_trades, 2)
        self.assertEqual([row["cross_id"] for row in same.trades], ["A", "B"])
        self.assertNotEqual(same.trades[1]["entry_time"], rows[2].start.isoformat())

    def test_no_additions_while_holding(self):
        rows = [candle(i) for i in range(4)]
        result = self.run_sim(rows, [decision(1), decision(2, cross="B"), decision(3, cross="C")])
        self.assertEqual(result.metrics.closed_trades, 0)
        self.assertEqual(result.metrics.total_exit_records, 1)
        self.assertEqual(result.trades[0]["quantity_btc"], 0.005)

    def test_minimum_btc_order_rejected(self):
        result = self.run_sim([candle(0, 100_000_000), candle(1, 100_000_000)])
        self.assertEqual(result.metrics.closed_trades, 0)
        self.assertEqual(result.metrics.rejected_entries, 1)
        self.assertEqual(result.metrics.net_profit_jpy, 0)

    def test_fees_inclusive_budget_and_cost_stress(self):
        rows = [candle(0), candle(1), candle(2)]
        first = self.run_sim(rows, multiplier=1)
        stress = self.run_sim(rows, multiplier=2)
        self.assertLessEqual(first.trades[0]["entry_outlay_jpy"], 50_000)
        self.assertGreater(first.metrics.execution_cost_jpy, 0)
        self.assertLess(stress.metrics.net_profit_jpy, first.metrics.net_profit_jpy)
        self.assertLess(first.metrics.net_profit_jpy, 0)

    def test_forced_end_is_distinct_from_mark_to_market(self):
        result = self.run_sim([candle(0), candle(1), candle(2, close=10_100_000, high=10_100_000)], multiplier=1)
        self.assertEqual(result.metrics.forced_end_exits, 1)
        self.assertEqual(result.metrics.closed_trades, 0)
        self.assertEqual(result.metrics.realized_pnl_jpy, 0)
        self.assertAlmostEqual(result.metrics.net_profit_jpy, result.metrics.period_end_pnl_jpy)
        self.assertEqual(result.trades[0]["exit_reason"], "forced_end")
        self.assertTrue(result.trades[0]["forced_end"])
        self.assertGreater(result.metrics.open_position_btc, 0)
        self.assertGreater(result.metrics.mark_to_market_equity_jpy, result.metrics.liquidated_end_equity_jpy)
        self.assertEqual(result.metrics.ending_equity_jpy, result.metrics.liquidated_end_equity_jpy)
        json.dumps(result.to_dict(), allow_nan=False)

    def test_50000_and_13000_include_nonzero_fees(self):
        rows = dataset([candle(0), candle(1), candle(2)])
        with patch.dict(POLICY["execution"], {"fee_bps_per_side": 10.0}):
            for budget in (50000, 13000):
                with self.subTest(budget=budget):
                    result = simulate(rows, BASELINE, [decision(1)], start_index=1,
                                      end_index=3, buy_budget_jpy=budget)
                    trade = result.trades[0]
                    self.assertGreater(trade["fees_jpy"], 0)
                    self.assertLessEqual(trade["entry_outlay_jpy"], budget)
                    self.assertGreater(trade["entry_outlay_jpy"], budget - 1)
                    self.assertAlmostEqual(trade["entry_outlay_jpy"],
                                           trade["quantity_btc"] * trade["entry_price_jpy"] * 1.001)

    def test_cash_below_budget_reduces_size_or_rejects_minimum(self):
        rows = dataset([candle(0), candle(1), candle(2)])
        with patch.dict(POLICY, {"initial_cash_jpy": 12000}), \
             patch.dict(POLICY["execution"], {"fee_bps_per_side": 10.0}):
            result = simulate(rows, BASELINE, [decision(1)], start_index=1,
                              end_index=3, buy_budget_jpy=13000)
            self.assertEqual(result.metrics.total_exit_records, 1)
            self.assertLessEqual(result.trades[0]["entry_outlay_jpy"], 12000)
            self.assertGreater(result.trades[0]["entry_outlay_jpy"], 11999)
        with patch.dict(POLICY, {"initial_cash_jpy": 4000}):
            result = simulate(rows, BASELINE, [decision(1)], start_index=1,
                              end_index=3, buy_budget_jpy=13000)
            self.assertEqual(result.metrics.total_exit_records, 0)
            self.assertEqual(result.metrics.rejected_entries, 1)

    def test_minimum_sell_is_rejected_but_end_valuation_is_separate(self):
        rows = [candle(0), candle(1), candle(2, opening=1000)]
        result = self.run_sim(rows)
        self.assertEqual(result.metrics.rejected_exits, 1)
        self.assertEqual(result.metrics.closed_trades, 0)
        self.assertEqual(result.metrics.forced_end_exits, 1)
        self.assertGreater(result.metrics.open_position_btc, 0)
        self.assertEqual(result.trades[0]["exit_reason"], "forced_end")

    def test_last_close_cannot_fill_without_following_bar(self):
        rows = [candle(0), candle(1)]
        result = self.run_sim(rows, [decision(2)])
        self.assertEqual(result.metrics.closed_trades, 0)
        self.assertIsNone(result.metrics.profit_factor)
        json.dumps(result.to_dict(), allow_nan=False)

    def test_exposure_holding_and_drawdown_are_account_based(self):
        result = self.run_sim([candle(0), candle(1), candle(2, low=9_600_000), candle(3)])
        self.assertAlmostEqual(result.metrics.exposure_pct, 200 / 3)
        self.assertAlmostEqual(result.metrics.max_holding_hours, 10 / 60)
        self.assertAlmostEqual(result.metrics.net_profit_jpy, -1500)
        self.assertAlmostEqual(result.metrics.max_drawdown_pct, 1.5)
        self.assertEqual(result.metrics.profit_factor, 0)

    def test_invalid_execution_inputs_rejected(self):
        rows = dataset([candle(0), candle(1)])
        with self.assertRaises(LabError):
            simulate(rows, BASELINE, [decision(1), decision(1)], start_index=1, end_index=2)
        with self.assertRaises(LabError):
            simulate(rows, BASELINE, [decision(1)], start_index=1, end_index=2, cost_multiplier=math.nan)
        with self.assertRaises(LabError):
            simulate(rows, BASELINE, [decision(1)], start_index=1, end_index=2, buy_budget_jpy=50_001)


if __name__ == "__main__":
    unittest.main()
