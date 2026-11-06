"""Research orchestration boundaries, using synthetic data and fixed metrics.

These tests exercise the real engine, validator, screening and SQLite ledger.
The provider and evaluator responses are fixtures: no claimed trading result,
external service, API key or running Bot is involved.
"""
import copy
import signal
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest import mock

from ai_lab import contracts, engine
from ai_lab.contracts import BASELINE, LabError, POLICY, ResearchDeadline
from ai_lab.dataset import chronological_split, make_fixture
from ai_lab.ledger import Ledger
from ai_lab.provider import FixtureProvider


def metrics(profit):
    return {
        "net_profit_jpy": profit,
        "closed_trades": 25,
        "max_drawdown_pct": 1.0,
        "max_single_trade_profit_share": 0.1,
    }


class EngineBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_directory = tempfile.TemporaryDirectory(prefix="ai-engine-input-")
        cls.dataset = make_fixture(
            Path(cls.fixture_directory.name) / "synthetic.csv", days=65, seed=73
        )
        cls.splits = chronological_split(cls.dataset)

    @classmethod
    def tearDownClass(cls):
        cls.fixture_directory.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ai-engine-ledger-")
        self.addCleanup(self.directory.cleanup)
        self.ledger = Ledger(Path(self.directory.name) / "research")
        self.addCleanup(self.ledger.close)
        self.contexts = []

        # State-machine tests should not depend on concurrent source-file edits.
        # A changed source identity is explicitly tested as a resume failure.
        identity_patch = mock.patch.object(
            engine,
            "source_identity",
            return_value={"source_hash": "fixture-v1", "files": {}, "python_version": "fixture"},
        )
        self.identity = identity_patch.start()
        self.addCleanup(identity_patch.stop)

        evaluate_patch = mock.patch.object(engine, "_evaluate", side_effect=self._evaluate)
        self.evaluate = evaluate_patch.start()
        self.addCleanup(evaluate_patch.stop)
        provider_patch = mock.patch.object(
            FixtureProvider, "propose", autospec=True, side_effect=self._propose
        )
        self.propose = provider_patch.start()
        self.addCleanup(provider_patch.stop)

    @staticmethod
    def candidate(index):
        value = BASELINE.to_dict()
        value.update(name=f"engine-fixture-{index}", ema_fast=10 + index)
        return value

    def _propose(self, provider, context, index):
        self.contexts.append((index, copy.deepcopy(context)))
        return self.candidate(index)

    def _evaluate(self, dataset, candidate, start, end, budget, multiplier=1.0):
        profit = 0 if candidate.strategy_hash == BASELINE.strategy_hash else 1000 + candidate.ema_fast
        return metrics(profit)

    def run_lab(self, *, max_trials=2, **kwargs):
        return engine.run_research(
            self.dataset,
            self.ledger,
            "engine-audit",
            max_trials=max_trials,
            provider=FixtureProvider(),
            **kwargs,
        )

    def final_calls(self):
        holdout = self.splits["holdout"]
        return [call for call in self.evaluate.call_args_list if tuple(call.args[2:4]) == holdout]

    def test_provider_observes_committed_trial_reservation(self):
        def after_reservation(provider, context, index):
            # A separate connection cannot see an uncommitted reservation.
            with closing(sqlite3.connect(self.ledger.root / "research.sqlite3")) as reader:
                status = reader.execute(
                    "SELECT status FROM trials WHERE run_id=? AND trial_index=?",
                    ("engine-audit", index),
                ).fetchone()
                recorded = reader.execute(
                    "SELECT count(*) FROM events WHERE run_id=? AND kind='GENERATOR_INPUT'",
                    ("engine-audit",),
                ).fetchone()[0]
            self.assertEqual(status, ("RESERVED",))
            self.assertEqual(recorded, index + 1)
            return self._propose(provider, context, index)

        self.propose.side_effect = after_reservation
        self.run_lab()
        self.assertEqual(self.propose.call_count, 2)

    def test_failed_reservation_never_invokes_provider(self):
        with mock.patch.object(self.ledger, "reserve_trial", side_effect=LabError("RESERVATION_FAILED")):
            with self.assertRaisesRegex(LabError, "RESERVATION_FAILED"):
                self.run_lab()
        self.propose.assert_not_called()
        self.assertEqual(self.ledger.trials("engine-audit"), [])
        self.assertEqual(self.final_calls(), [])

    def test_deadline_inside_candidate_json_stops_the_entire_run(self):
        original_json = contracts.canonical_json
        invoked = []
        original_handler = signal.getsignal(signal.SIGALRM)

        def interrupt_candidate(value):
            if isinstance(value, dict) and value.get("name") == "engine-fixture-0":
                invoked.append(True)
                # Trigger the actual installed deadline handler inside the
                # validation try/except, without a timing-sensitive sleep.
                handler = signal.getsignal(signal.SIGALRM)
                self.assertTrue(callable(handler))
                handler(signal.SIGALRM, None)
            return original_json(value)

        with mock.patch.object(contracts, "canonical_json", side_effect=interrupt_candidate):
            with self.assertRaisesRegex(ResearchDeadline, "TIME_LIMIT"):
                self.run_lab()

        self.assertEqual(invoked, [True])
        self.assertEqual(self.propose.call_count, 1)
        self.assertEqual([row["status"] for row in self.ledger.trials("engine-audit")], ["RESERVED"])
        self.assertEqual(self.ledger.snapshot("engine-audit")["final_tests"], [])
        self.assertEqual(signal.getsignal(signal.SIGALRM), original_handler)

    def test_reserved_trial_is_consumed_without_regenerating(self):
        calls = []

        def interrupted_generation(provider, context, index):
            calls.append(index)
            raise KeyboardInterrupt()

        self.propose.side_effect = interrupted_generation
        with self.assertRaises(KeyboardInterrupt):
            self.run_lab()
        self.assertEqual(self.ledger.trials("engine-audit")[0]["status"], "RESERVED")

        def resumed_generation(provider, context, index):
            calls.append(index)
            return self._propose(provider, context, index)

        self.propose.side_effect = resumed_generation
        snapshot = self.run_lab()
        self.assertEqual(calls, [0, 1])
        self.assertEqual(len(snapshot["trials"]), 2)
        self.assertEqual(snapshot["trials"][0]["error_code"], "INTERRUPTED_GENERATION")
        self.assertEqual(snapshot["trials"][0]["status"], "FAILED")

    def test_generated_trial_reuses_saved_candidate_after_crash(self):
        interrupted = []

        def evaluation_crash(dataset, candidate, start, end, budget, multiplier=1.0):
            if candidate.strategy_hash != BASELINE.strategy_hash and not interrupted:
                interrupted.append(candidate.strategy_hash)
                raise KeyboardInterrupt()
            return self._evaluate(dataset, candidate, start, end, budget, multiplier)

        self.evaluate.side_effect = evaluation_crash
        with self.assertRaises(KeyboardInterrupt):
            self.run_lab(max_trials=1)
        before = self.ledger.trials("engine-audit")[0]
        self.assertEqual(before["status"], "GENERATED")
        self.assertEqual(before["candidate_hash"], interrupted[0])

        self.evaluate.side_effect = self._evaluate
        snapshot = self.run_lab(max_trials=1)
        self.assertEqual(self.propose.call_count, 1)
        self.assertEqual(snapshot["trials"][0]["candidate_hash"], before["candidate_hash"])
        self.assertEqual(snapshot["trials"][0]["status"], "SHORTLISTED")

    def test_completed_run_does_not_generate_or_evaluate_again(self):
        first = self.run_lab(max_trials=1)
        self.propose.reset_mock()
        self.evaluate.reset_mock()
        second = self.run_lab(max_trials=1)
        self.propose.assert_not_called()
        self.evaluate.assert_not_called()
        self.assertEqual(second["run"]["result"], first["run"]["result"])
        self.assertTrue(second["event_chain_ok"])

    def test_changed_manifest_refuses_resume_before_generation(self):
        self.run_lab(max_trials=1)
        self.propose.reset_mock()
        self.evaluate.reset_mock()
        for changed in (
            {"max_trials": 1, "buy_budget_jpy": 13000},
            {"max_trials": 2},
            {"max_trials": 1, "fixture_seed": 123},
        ):
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(LabError, "RESUME_MANIFEST_MISMATCH"):
                    self.run_lab(**changed)
        with self.assertRaisesRegex(LabError, "RESUME_MANIFEST_MISMATCH"):
            engine.run_research(replace(self.dataset, sha256="f" * 64), self.ledger,
                                "engine-audit", max_trials=1, provider=FixtureProvider())
        with mock.patch.object(engine, "POLICY_HASH", "f" * 64):
            with self.assertRaisesRegex(LabError, "RESUME_MANIFEST_MISMATCH"):
                self.run_lab(max_trials=1)
        self.identity.return_value = {"source_hash": "fixture-v2", "files": {}, "python_version": "fixture"}
        with self.assertRaisesRegex(LabError, "RESUME_MANIFEST_MISMATCH"):
            self.run_lab(max_trials=1)
        self.propose.assert_not_called()
        self.evaluate.assert_not_called()

    def test_development_screening_always_receives_three_folds(self):
        real_screen = engine.screen
        with mock.patch.object(engine, "screen", wraps=real_screen) as screening:
            self.run_lab()
        dev_calls = [call for call in screening.call_args_list if len(call.args) == 5]
        final_calls = [call for call in screening.call_args_list if len(call.args) == 4]
        self.assertEqual(len(dev_calls), 2)
        self.assertTrue(all(len(call.args[4]) == 3 for call in dev_calls))
        self.assertEqual(len(final_calls), 1)

        dev_start, dev_end = self.splits["development"]
        for index in range(2):
            candidate_hash = contracts.validate_candidate(self.candidate(index)).strategy_hash
            actual = [tuple(call.args[2:4]) for call in self.evaluate.call_args_list
                      if call.args[1].strategy_hash == candidate_hash
                      and dev_start <= call.args[2] < call.args[3] <= dev_end
                      and tuple(call.args[2:4]) != (dev_start, dev_end)]
            self.assertEqual(len(actual), 3)
            self.assertEqual(actual[0][0], dev_start)
            self.assertEqual(actual[-1][1], dev_end)
            self.assertTrue(all(left[1] == right[0] for left, right in zip(actual, actual[1:])))
            for start, end in actual:
                for boundary in (start, end):
                    stamp = self.dataset.candles[boundary].start
                    self.assertEqual((stamp.hour % 4, stamp.minute, stamp.second), (0, 0, 0))

    def test_holdout_runs_only_one_winner_after_reservation(self):
        holdout = self.splits["holdout"]
        observed = []

        def holdout_failure(dataset, candidate, start, end, budget, multiplier=1.0):
            if (start, end) == holdout:
                row = self.ledger.cn.execute("SELECT status,candidate_hash FROM final_tests").fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["status"], "RESERVED")
                observed.append((candidate.strategy_hash, multiplier))
                return metrics(0 if candidate.strategy_hash == BASELINE.strategy_hash else -500)
            return self._evaluate(dataset, candidate, start, end, budget, multiplier)

        self.evaluate.side_effect = holdout_failure
        snapshot = self.run_lab()
        winner = contracts.validate_candidate(self.candidate(1)).strategy_hash
        runner_up = contracts.validate_candidate(self.candidate(0)).strategy_hash
        stress = POLICY["screening"]["stress_cost_multiplier"]
        self.assertEqual(observed, [(winner, 1.0), (winner, stress),
                                    (BASELINE.strategy_hash, 1.0), (BASELINE.strategy_hash, stress)])
        self.assertNotIn(runner_up, [item[0] for item in observed])
        self.assertEqual(snapshot["run"]["result"]["outcome"], "HOLDOUT_FAILED")
        self.assertEqual(len(snapshot["final_tests"]), 1)
        self.assertEqual(self.propose.call_count, 2)

    def test_feedback_contains_development_results_only(self):
        final_only_profit = 246813579

        def tagged_metrics(dataset, candidate, start, end, budget, multiplier=1.0):
            if (start, end) == self.splits["holdout"]:
                return metrics(0 if candidate.strategy_hash == BASELINE.strategy_hash else final_only_profit)
            return self._evaluate(dataset, candidate, start, end, budget, multiplier)

        self.evaluate.side_effect = tagged_metrics
        self.run_lab()
        self.assertEqual(len(self.contexts), 2)
        self.assertEqual(self.contexts[0][1]["development_feedback"], [])
        second = self.contexts[1][1]
        self.assertEqual(len(second["development_feedback"]), 1)
        self.assertEqual(second["development_feedback"][0]["net_profit_jpy"], 1010)
        self.assertEqual(set(second), {"training_summary", "development_feedback", "parent_candidate",
                                       "trial_index", "policy_version"})
        for _, context in self.contexts:
            for result in context["development_feedback"]:
                self.assertNotEqual(result["net_profit_jpy"], final_only_profit)
                self.assertEqual(set(result), {"candidate_hash", "net_profit_jpy", "max_drawdown_pct",
                                               "closed_trades", "gate_reasons"})
        self.assertEqual(self.propose.call_count, 2)

    def test_interrupted_holdout_is_not_repeated_on_resume(self):
        def interrupt_final(dataset, candidate, start, end, budget, multiplier=1.0):
            if (start, end) == self.splits["holdout"]:
                raise KeyboardInterrupt()
            return self._evaluate(dataset, candidate, start, end, budget, multiplier)

        self.evaluate.side_effect = interrupt_final
        with self.assertRaises(KeyboardInterrupt):
            self.run_lab(max_trials=1)
        self.assertEqual(self.ledger.snapshot("engine-audit")["final_tests"][0]["status"], "RESERVED")
        self.assertEqual(len(self.final_calls()), 1)

        self.evaluate.side_effect = self._evaluate
        self.evaluate.reset_mock()
        self.propose.reset_mock()
        snapshot = self.run_lab(max_trials=1)
        self.assertEqual(snapshot["run"]["result"]["outcome"], "HOLDOUT_ALREADY_CONSUMED")
        self.assertEqual(self.final_calls(), [])
        self.propose.assert_not_called()

    def test_completed_holdout_is_reused_after_run_completion_crash(self):
        with mock.patch.object(self.ledger, "finish_run", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.run_lab(max_trials=1)
        before = self.ledger.snapshot("engine-audit")
        self.assertEqual(before["run"]["status"], "RUNNING")
        self.assertEqual(before["final_tests"][0]["status"], "COMPLETED")

        self.evaluate.reset_mock()
        self.propose.reset_mock()
        after = self.run_lab(max_trials=1)
        self.assertEqual(after["run"]["status"], "COMPLETED")
        self.assertEqual(after["run"]["result"]["final_test"], before["final_tests"][0]["result"])
        self.assertEqual(self.final_calls(), [])
        self.propose.assert_not_called()

    def test_invalid_and_duplicate_candidates_consume_the_trial_limit(self):
        def varied_proposal(provider, context, index):
            if index == 0:
                return BASELINE.to_dict()
            if index == 1:
                value = self.candidate(index)
                value["python"] = "not executable"
                return value
            return self._propose(provider, context, index)

        self.propose.side_effect = varied_proposal
        snapshot = self.run_lab(max_trials=3)
        self.assertEqual(self.propose.call_count, 3)
        self.assertEqual(len(snapshot["trials"]), 3)
        self.assertEqual([row["status"] for row in snapshot["trials"]],
                         ["DUPLICATE", "FAILED", "SHORTLISTED"])

    def test_invalid_trial_limits_never_invoke_provider(self):
        for limit in (True, 0, POLICY["max_trials"] + 1):
            with self.subTest(limit=limit):
                with self.assertRaisesRegex(LabError, "TRIAL_LIMIT"):
                    self.run_lab(max_trials=limit)
        self.propose.assert_not_called()
        self.evaluate.assert_not_called()

    def test_unsupported_providers_are_rejected_without_invocation(self):
        class FixtureSubclass(FixtureProvider):
            pass

        for provider in (object(), FixtureSubclass(), False, {}, 0):
            with self.subTest(provider_type=type(provider).__name__):
                with self.assertRaisesRegex(LabError, "LIVE_PROVIDER_DISABLED"):
                    engine.run_research(self.dataset, self.ledger, "engine-audit",
                                        max_trials=1, provider=provider)
        self.propose.assert_not_called()
        self.evaluate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
