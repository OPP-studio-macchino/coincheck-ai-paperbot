"""File handoff and offline evaluation boundaries. No real keys or HTTP calls."""
from __future__ import annotations

import copy
import io
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest import mock

from ai_lab import engine
from ai_lab.__main__ import main
from ai_lab.artifact_provider import ArtifactProvider
from ai_lab.contracts import BASELINE, LabError, canonical_json, digest, strict_json, validate_candidate
from ai_lab.dataset import make_fixture
from ai_lab.ledger import Ledger
from ai_lab.phase2_artifacts import (load_candidate, read_envelope, read_json_file,
                                    save_candidate, validate_artifact, write_json_file)
from ai_lab.phase2_policy import MODEL, PRICE_HASH, RESERVATION_MICROUSD, usage_cost
from ai_lab.proposal_context import build_request, validate_envelope
from ai_lab.provider import FixtureProvider
from ai_lab.report import write_report


def completed_receipt(envelope, candidate, *, mode="fixture"):
    usage = {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200}
    return {"mode": mode, "request_hash": envelope["binding"]["request_hash"],
            "context_hash": envelope["binding"]["context_hash"], "status": "COMPLETED",
            "attempt_id": envelope["binding"]["request_hash"], "model": MODEL,
            "response_id": "resp_fixture_test" if mode == "fixture" else "resp_test_claim",
            "usage": usage, "budget_policy_hash": "b" * 64, "price_hash": PRICE_HASH,
            "reserved_microusd": RESERVATION_MICROUSD,
            "actual_microusd": usage_cost(MODEL, usage),
            "created_at_utc": "2026-11-06T00:00:00+00:00",
            "completed_at_utc": "2026-11-06T00:00:01+00:00",
            "candidate_hash": validate_candidate(candidate).strategy_hash,
            "response_hash": digest({"fixture_candidate": candidate})}


def fixed_metrics(_dataset, candidate, _start, _end, _budget, multiplier=1.0):
    return {"net_profit_jpy": 0 if candidate.strategy_hash == BASELINE.strategy_hash else 1000,
            "closed_trades": 25, "max_drawdown_pct": 1.0, "max_single_trade_profit_share": .1}


class ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = tempfile.TemporaryDirectory(prefix="ai-artifact-input-")
        cls.csv = Path(cls.inputs.name) / "synthetic.csv"
        cls.data = make_fixture(cls.csv, days=65, seed=73)

    @classmethod
    def tearDownClass(cls):
        cls.inputs.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ai-artifact-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        identity = mock.patch.object(engine, "source_identity", return_value={
            "source_hash": "a" * 64, "files": {}, "python_version": "fixture"})
        identity.start()
        self.addCleanup(identity.stop)
        self.envelope = engine.prepare_proposal(self.data, buy_budget_jpy=13000)
        self.candidate = FixtureProvider().propose(self.envelope["context"], 0)

    def artifact(self, *, candidate=None, mode="fixture", root=None):
        value = candidate or self.candidate
        target = save_candidate(root or self.root / "artifacts", self.envelope, value,
                                generation=completed_receipt(self.envelope, value, mode=mode))
        return load_candidate(target), target

    def test_prepare_contains_train_only_model_input(self):
        wire = build_request(self.envelope)
        sent = strict_json(wire["input"][1]["content"])
        self.assertEqual(sent, self.envelope["context"])
        self.assertEqual(sent["trial_index"], 0)
        self.assertEqual(sent["development_feedback"], [])
        self.assertNotIn("binding", sent)
        self.assertNotIn("holdout", sent)
        self.assertEqual(wire["truncation"], "disabled")
        self.assertEqual(wire["service_tier"], "default")
        self.assertEqual(wire["tools"], [])
        self.assertFalse(wire["store"])
        self.assertEqual(self.envelope["binding"]["request_hash"], digest(wire))

    def test_holdout_changes_do_not_change_model_request_but_break_data_binding(self):
        data = replace(self.data, sha256="f" * 64)
        different = engine.prepare_proposal(data, buy_budget_jpy=13000)
        self.assertEqual(build_request(different), build_request(self.envelope))
        self.assertNotEqual(different["envelope_hash"], self.envelope["envelope_hash"])
        artifact, _ = self.artifact()
        with self.assertRaisesRegex(LabError, "PHASE2_ARTIFACT_CONTEXT_MISMATCH"):
            validate_artifact(artifact, expected_envelope=different)

    def test_frozen_budget_and_source_must_match_before_trial_reservation(self):
        artifact, _ = self.artifact()
        with LedgerClosing(self.root / "ledger") as ledger:
            for budget, source in ((50000, "a" * 64), (13000, "c" * 64)):
                with self.subTest(budget=budget, source=source), mock.patch.object(
                    engine, "source_identity", return_value={"source_hash": source, "files": {}, "python_version": "fixture"}
                ), mock.patch.object(engine, "_evaluate") as evaluate:
                    with self.assertRaisesRegex(LabError, "PHASE2_ARTIFACT_CONTEXT_MISMATCH"):
                        engine.run_research(self.data, ledger, "bound", max_trials=1,
                                            buy_budget_jpy=budget, provider=ArtifactProvider(artifact))
                    evaluate.assert_not_called()
                    self.assertEqual(ledger.cn.execute("SELECT count(*) FROM runs").fetchone()[0], 0)

    def test_model_policy_schema_and_context_tampering_are_rejected(self):
        for field in ("policy_hash", "source_hash", "wire_schema_hash", "request_hash", "model"):
            changed = copy.deepcopy(self.envelope)
            changed["binding"][field] = "z" * 64
            with self.subTest(field=field), self.assertRaises(LabError):
                validate_envelope(changed)
        changed = copy.deepcopy(self.envelope)
        changed["context"]["training_summary"]["return_pct"] += 1
        with self.assertRaises(LabError):
            validate_envelope(changed)
        changed = copy.deepcopy(self.envelope)
        changed["context"]["holdout"] = {"profit": 99999}
        with self.assertRaises(LabError):
            validate_envelope(changed)

    def test_candidate_and_receipt_cannot_be_silently_replaced(self):
        original, _ = self.artifact()
        for change in ("candidate", "candidate_hash", "price", "usage", "receipt_status", "attempt_id", "unknown"):
            item = copy.deepcopy(original)
            if change == "candidate":
                item["candidate"]["ema_fast"] += 1
            elif change == "candidate_hash":
                item["candidate_hash"] = "f" * 64
            elif change == "price":
                item["generation"]["price_hash"] = "f" * 64
            elif change == "usage":
                item["generation"]["usage"]["output_tokens"] += 1
            elif change == "receipt_status":
                item["generation"]["status"] = "RESERVED"
            elif change == "attempt_id":
                item["generation"]["attempt_id"] = "f" * 64
            else:
                item["python"] = "raise RuntimeError('must never execute')"
            # Rehashing alone must not bypass semantic validation.
            item["artifact_hash"] = digest({k: v for k, v in item.items() if k != "artifact_hash"})
            with self.subTest(change=change), self.assertRaises(LabError):
                validate_artifact(item)

    def test_response_id_limits_match_budget_and_fixture_prefix_is_distinct(self):
        artifact, _ = self.artifact(mode="openai_responses")
        for size in (135, 160, 161):
            item = copy.deepcopy(artifact)
            item["generation"]["response_id"] = "resp_" + "a" * (size - 5)
            item["artifact_hash"] = digest({k: v for k, v in item.items() if k != "artifact_hash"})
            with self.subTest(size=size):
                if size <= 160:
                    self.assertEqual(validate_artifact(item), item)
                else:
                    with self.assertRaisesRegex(LabError, "PHASE2_GENERATION_ID"):
                        validate_artifact(item)
        for mode, response_id in (("fixture", "resp_other"), ("openai_responses", "resp_fixture_wrong_origin")):
            item = copy.deepcopy(artifact)
            item["generation"].update(mode=mode, response_id=response_id)
            item["artifact_hash"] = digest({k: v for k, v in item.items() if k != "artifact_hash"})
            with self.subTest(mode=mode), self.assertRaises(LabError):
                validate_artifact(item)

    def test_immutable_file_idempotence_permissions_and_conflict(self):
        artifact, target = self.artifact()
        before = target.stat()
        again, same = self.artifact()
        self.assertEqual(again, artifact)
        self.assertEqual(same, target)
        self.assertEqual((target.stat().st_ino, target.stat().st_mtime_ns), (before.st_ino, before.st_mtime_ns))
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(target.parent.stat().st_mode), 0o700)
        changed = dict(self.candidate, ema_fast=self.candidate["ema_fast"] + 1)
        with self.assertRaisesRegex(LabError, "PHASE2_HANDOFF_CONFLICT"):
            self.artifact(candidate=changed)
        self.assertEqual(load_candidate(target), artifact)

    def test_json_duplicate_nonfinite_oversized_and_nonregular_inputs_fail_closed(self):
        path = self.root / "bad.json"
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b' ' * 65537):
            path.write_bytes(raw)
            with self.assertRaises(LabError):
                read_json_file(path)
        fifo = self.root / "fifo"
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(LabError):
            read_json_file(fifo)
        with self.assertRaises(LabError):
            read_json_file(self.root)

    def test_symlink_final_parent_and_traversal_cannot_read_or_replace_targets(self):
        artifact, path = self.artifact()
        linked = self.root / "link.json"
        linked.symlink_to(path)
        with self.assertRaises(LabError):
            load_candidate(linked)
        directory_link = self.root / "linked-dir"
        directory_link.symlink_to(path.parent, target_is_directory=True)
        with self.assertRaises(LabError):
            load_candidate(directory_link / path.name)
        with self.assertRaises(LabError):
            write_json_file(linked, {"overwrite": True})
        with self.assertRaises(LabError):
            load_candidate(path.parent / ".." / "artifacts" / path.name)
        self.assertEqual(load_candidate(path), artifact)

    def test_shared_writable_inputs_and_nonprivate_output_directories_rejected(self):
        path = self.root / "input.json"
        path.write_text('{}')
        path.chmod(0o666)
        with self.assertRaises(LabError):
            read_json_file(path)
        shared = self.root / "shared"
        shared.mkdir(mode=0o755)
        with self.assertRaises(LabError):
            write_json_file(shared / "out.json", {})
        path.chmod(0o444)
        self.assertEqual(read_json_file(path), {})

    def test_failed_publication_leaves_no_complete_artifact_or_secret_exception(self):
        with mock.patch("ai_lab.phase2_artifacts.os.link", side_effect=OSError("secret-do-not-print")):
            with self.assertRaisesRegex(LabError, "^PHASE2_HANDOFF_STORE$"):
                self.artifact()
        self.assertEqual(list((self.root / "artifacts").iterdir()), [])

    def test_single_candidate_and_exact_provider_boundary(self):
        artifact, _ = self.artifact()
        class Subclass(ArtifactProvider):
            pass
        with LedgerClosing(self.root / "ledger") as ledger:
            for provider, count in ((ArtifactProvider(artifact), 2), (Subclass(artifact), 1)):
                with self.subTest(provider=type(provider).__name__, count=count), self.assertRaises(LabError):
                    engine.run_research(self.data, ledger, "one-only", max_trials=count,
                                        buy_budget_jpy=13000, provider=provider)
            self.assertEqual(ledger.cn.execute("SELECT count(*) FROM runs").fetchone()[0], 0)

    def test_evaluation_replay_and_changed_artifact_have_distinct_identities(self):
        artifact, _ = self.artifact()
        with LedgerClosing(self.root / "ledger") as ledger, mock.patch.object(
            engine, "_evaluate", side_effect=fixed_metrics
        ) as evaluate, mock.patch("socket.socket", side_effect=AssertionError("NETWORK_FORBIDDEN")):
            first = engine.run_research(self.data, ledger, "artifact-run", max_trials=1,
                                        buy_budget_jpy=13000, provider=ArtifactProvider(artifact))
            self.assertEqual(first["run"]["manifest"]["provider_mode"], "offline_artifact")
            self.assertEqual(first["run"]["result"]["outcome"], "DEMO_ONLY")
            self.assertEqual(len(first["final_tests"]), 1)
            evaluate.reset_mock()
            replay = engine.run_research(self.data, ledger, "artifact-run", max_trials=1,
                                         buy_budget_jpy=13000, provider=ArtifactProvider(artifact))
            evaluate.assert_not_called()
            self.assertEqual(replay, first)
            changed = dict(self.candidate, ema_fast=self.candidate["ema_fast"] + 1)
            replacement, _ = self.artifact(candidate=changed, root=self.root / "replacement")
            with self.assertRaisesRegex(LabError, "RESUME_MANIFEST_MISMATCH"):
                engine.run_research(self.data, ledger, "artifact-run", max_trials=1,
                                    buy_budget_jpy=13000, provider=ArtifactProvider(replacement))
            evaluate.assert_not_called()
            other = engine.run_research(self.data, ledger, "other-run", max_trials=1,
                                        buy_budget_jpy=13000, provider=ArtifactProvider(artifact))
            self.assertEqual(other["run"]["result"]["outcome"], "HOLDOUT_ALREADY_CONSUMED")

    def test_interrupted_evaluation_reuses_saved_candidate_without_proposing_again(self):
        artifact, _ = self.artifact()
        def interrupted(dataset, candidate, start, end, budget, multiplier=1.0):
            if candidate.strategy_hash != BASELINE.strategy_hash:
                raise KeyboardInterrupt()
            return fixed_metrics(dataset, candidate, start, end, budget, multiplier)
        with LedgerClosing(self.root / "ledger") as ledger:
            with mock.patch.object(engine, "_evaluate", side_effect=interrupted):
                with self.assertRaises(KeyboardInterrupt):
                    engine.run_research(self.data, ledger, "interrupted", max_trials=1,
                                        buy_budget_jpy=13000, provider=ArtifactProvider(artifact))
            self.assertEqual(ledger.trials("interrupted")[0]["status"], "GENERATED")
            with mock.patch.object(engine, "_evaluate", side_effect=fixed_metrics), mock.patch.object(
                ArtifactProvider, "propose", side_effect=AssertionError("DO_NOT_REGENERATE")
            ):
                result = engine.run_research(self.data, ledger, "interrupted", max_trials=1,
                                             buy_budget_jpy=13000, provider=ArtifactProvider(artifact))
            self.assertEqual(result["run"]["status"], "COMPLETED")

    def test_mutated_saved_candidate_rejected_on_generated_and_completed_resume(self):
        artifact, _ = self.artifact()
        for state in ("GENERATED", "COMPLETED"):
            with self.subTest(state=state), LedgerClosing(self.root / state) as ledger:
                def interrupted(dataset, candidate, start, end, budget, multiplier=1.0):
                    if candidate.strategy_hash != BASELINE.strategy_hash:
                        raise KeyboardInterrupt()
                    return fixed_metrics(dataset, candidate, start, end, budget, multiplier)
                with mock.patch.object(engine, "_evaluate", side_effect=interrupted if state == "GENERATED" else fixed_metrics):
                    if state == "GENERATED":
                        with self.assertRaises(KeyboardInterrupt):
                            engine.run_research(self.data, ledger, "tamper", max_trials=1,
                                                buy_budget_jpy=13000, provider=ArtifactProvider(artifact))
                    else:
                        engine.run_research(self.data, ledger, "tamper", max_trials=1,
                                            buy_budget_jpy=13000, provider=ArtifactProvider(artifact))
                changed = dict(self.candidate)
                if state == "GENERATED":
                    changed["ema_fast"] += 1
                else:
                    changed["name"] = "renamed-after-completion"
                normalized = validate_candidate(changed)
                ledger.cn.execute("UPDATE trials SET candidate=?,candidate_hash=? WHERE run_id=?",
                                  (canonical_json(normalized.to_dict()), normalized.strategy_hash, "tamper"))
                # Existing event hashes still validate: the new guard must compare
                # state rows to the original immutable candidate artifact as well.
                self.assertTrue(ledger.verify_events())
                with mock.patch.object(engine, "_evaluate") as evaluate, self.assertRaisesRegex(
                    LabError, "PHASE2_LEDGER_CANDIDATE_MISMATCH"
                ):
                    engine.run_research(self.data, ledger, "tamper", max_trials=1,
                                        buy_budget_jpy=13000, provider=ArtifactProvider(artifact))
                evaluate.assert_not_called()

    def test_generation_origin_is_visible_but_not_claimed_authenticated(self):
        for mode in ("fixture", "openai_responses"):
            artifact, _ = self.artifact(mode=mode, root=self.root / mode)
            with LedgerClosing(self.root / (mode + "-ledger")) as ledger, mock.patch.object(
                engine, "_evaluate", side_effect=fixed_metrics
            ):
                snapshot = engine.run_research(self.data, ledger, "origin", max_trials=1,
                                               buy_budget_jpy=13000, provider=ArtifactProvider(artifact))
                manifest = snapshot["run"]["manifest"]
                self.assertEqual(manifest["actual_openai_calls"], 0)
                self.assertEqual(manifest["actual_openai_calls_scope"], "offline_evaluator_only")
                self.assertEqual(manifest["upstream_openai_calls_reported"], int(mode == "openai_responses"))
                self.assertFalse(manifest["producer_authenticated"])
                report = self.root / (mode + ".html")
                write_report(snapshot, report)
                page = report.read_text()
                if mode == "fixture":
                    self.assertIn("生成方式：固定応答", page)
                    self.assertIn("模擬値", page)
                else:
                    self.assertIn("OpenAI応答の保存候補", page)
                    self.assertIn("申告値", page)
                    self.assertNotIn("生成方式：固定応答", page)

    def test_cli_rederives_expected_data_before_creating_a_ledger(self):
        request = self.root / "prepared" / "request.json"
        with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["prepare-proposal", "--csv", str(self.csv), "--output", str(request),
                                   "--buy-budget-jpy", "13000"]), 0)
        self.assertEqual(read_envelope(request), self.envelope)
        _, artifact_path = self.artifact()
        workspace = self.root / "must-not-exist"
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(
            LabError, "PHASE2_ARTIFACT_CONTEXT_MISMATCH"
        ):
            main(["evaluate-artifact", "--csv", str(self.csv), "--artifact", str(artifact_path),
                  "--workspace", str(workspace), "--run-id", "bad-budget", "--buy-budget-jpy", "50000"])
        self.assertFalse(workspace.exists())
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "SECRET_NOT_LOGGED"}, clear=True):
            with self.assertRaisesRegex(LabError, "RESEARCH_CREDENTIALS_FORBIDDEN"):
                main(["evaluate-artifact", "--csv", str(self.csv), "--artifact", str(artifact_path),
                      "--workspace", str(workspace), "--run-id", "bad-key", "--buy-budget-jpy", "13000"])
        self.assertFalse(workspace.exists())

    def test_real_fixture_generation_to_real_offline_evaluator_with_network_denied(self):
        from ai_lab.phase2 import main as generator_main, generate
        limits = {"per_call_limit_microusd": 2200000, "daily_limit_microusd": 2200000,
                  "total_limit_microusd": 2200000, "daily_call_limit": 1, "total_call_limit": 1}
        limits_path = write_json_file(self.root / "limits.json", limits)
        state = self.root / "generator"
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "socket.socket", side_effect=AssertionError("NETWORK_FORBIDDEN")
        ), redirect_stdout(io.StringIO()):
            self.assertEqual(generator_main(["init", "--state", str(state), "--mode", "fixture",
                                             "--limits", str(limits_path)]), 0)
            artifact = generate(self.envelope, state_root=state, mode="fixture", environ={})
            self.assertEqual(generate(self.envelope, state_root=state, mode="fixture", environ={}), artifact)
            with LedgerClosing(self.root / "research") as ledger:
                snapshot = engine.run_research(self.data, ledger, "real-evaluator", max_trials=1,
                                               buy_budget_jpy=13000, provider=ArtifactProvider(artifact))
        self.assertEqual(snapshot["run"]["status"], "COMPLETED")
        self.assertEqual(len(snapshot["trials"]), 1)
        self.assertEqual(snapshot["run"]["manifest"]["generation_mode"], "fixture")
        self.assertEqual(snapshot["run"]["manifest"]["provenance"]["source"], "synthetic")
        self.assertEqual(snapshot["run"]["result"]["actual_openai_calls"], 0)
        self.assertTrue(snapshot["event_chain_ok"])


class LedgerClosing:
    def __init__(self, path):
        self.path = path
    def __enter__(self):
        self.ledger = Ledger(self.path)
        return self.ledger
    def __exit__(self, *_):
        self.ledger.close()


if __name__ == "__main__":
    unittest.main()
