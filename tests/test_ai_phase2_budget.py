"""Real-process budget races and crash recovery; no sockets or API calls."""
from __future__ import annotations

import contextlib
import io
import multiprocessing
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from ai_lab.contracts import LabError, digest
from ai_lab.phase2_budget import (
    DB_NAME, MARKER_NAME, Phase2Budget, initialize, validate_limits,
)
from ai_lab.phase2_policy import (
    MAX_INPUT_TOKENS, MAX_OUTPUT_TOKENS, MODEL, PRICE_HASH, RESERVATION_MICROUSD,
    normalize_usage, price_record, usage_cost,
)

NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)
R = RESERVATION_MICROUSD


def limits(*, per_call=R, daily=8 * R, total=8 * R, daily_calls=8, total_calls=8):
    return dict(per_call_limit_microusd=per_call, daily_limit_microusd=daily,
                total_limit_microusd=total, daily_call_limit=daily_calls, total_call_limit=total_calls)


def completed(budget, key, *, now=NOW + timedelta(seconds=2)):
    return budget.finish(key, "COMPLETED", response_id="resp_fixture_budget", model=MODEL,
                         usage={"input_tokens": 12, "output_tokens": 34, "total_tokens": 46},
                         candidate_hash="c" * 64, response_hash="d" * 64, now=now)


def race_worker(root, number, same, barrier, output):
    try:
        with Phase2Budget(Path(root)) as budget:
            barrier.wait(timeout=15)
            result = budget.reserve(f"{1 if same else number + 1:064x}", "b" * 64,
                                    now=NOW + timedelta(seconds=1))
            output.put(("new" if result["new"] else "existing", None))
    except LabError as exc:
        output.put(("error", str(exc)))
    except BaseException:
        output.put(("unexpected", None))


def crash_worker(root, before_commit):
    with Phase2Budget(Path(root)) as budget:
        if before_commit:
            with patch("ai_lab.phase2_budget._event", side_effect=lambda *_: os._exit(74)):
                budget.reserve("a" * 64, "b" * 64, now=NOW + timedelta(seconds=1))
        else:
            budget.reserve("a" * 64, "b" * 64, now=NOW + timedelta(seconds=1))
            os._exit(73)


class Phase2BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # macOS /var is a symlink; use its real private temporary directory.
        self.root = Path(self.temp.name).resolve() / "state"

    def tearDown(self):
        self.temp.cleanup()

    def init(self, caps=None, *, root=None):
        return initialize(root or self.root, caps or limits(), now=NOW)

    def test_fixed_reservation_and_usage_are_not_caller_estimates(self):
        self.assertEqual(R, 2_111_536)
        self.assertEqual(PRICE_HASH, digest(price_record()))
        self.assertEqual(usage_cost(MODEL, {"input_tokens": 12, "output_tokens": 34, "total_tokens": 46}), 296)
        usage = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
                 "input_tokens_details": {"cached_tokens": 99},
                 "output_tokens_details": {"reasoning_tokens": 0}}
        self.assertEqual(usage_cost(MODEL, usage), 600)  # No cached-token discount/refund.
        self.assertEqual(normalize_usage(usage), {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
        self.init()
        with Phase2Budget(self.root) as budget:
            with self.assertRaises(TypeError):
                budget.reserve("a" * 64, "b" * 64, estimated_microusd=1, now=NOW)
            self.assertEqual(budget.status(now=NOW)["total_attempts"], 0)
            self.assertEqual(budget.reserve("a" * 64, "b" * 64, now=NOW)["record"]["reserved_microusd"], R)

    def test_explicit_init_private_paths_and_no_reset(self):
        with self.assertRaises(LabError):
            Phase2Budget(self.root)
        self.assertFalse(self.root.exists())
        self.init(root=self.root / "nested" / "live")
        root = self.root / "nested" / "live"
        for directory in (self.root, self.root / "nested", root):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        for name in (DB_NAME, MARKER_NAME):
            self.assertEqual((root / name).stat().st_mode & 0o777, 0o600)
        before = (root / DB_NAME).read_bytes()
        with self.assertRaisesRegex(LabError, "ALREADY_EXISTS"):
            initialize(root, limits(total=9 * R, total_calls=9), now=NOW)
        self.assertEqual((root / DB_NAME).read_bytes(), before)

    def test_invalid_limits_fail_before_creating_state(self):
        for bad in (0, -1, True, 1.5, "2", None, 10 ** 40):
            for key in limits():
                caps = limits()
                caps[key] = bad
                with self.subTest(key=key, bad_type=type(bad).__name__):
                    with self.assertRaises(LabError):
                        initialize(self.root, caps, now=NOW)
                    self.assertFalse(self.root.exists())
        for caps in ({}, {**limits(), "estimated_microusd": 1},
                     limits(per_call=2 * R, daily=R), limits(daily=9 * R, total=8 * R),
                     limits(daily_calls=9, total_calls=8)):
            with self.assertRaises(LabError):
                validate_limits(caps)

    def test_lower_than_required_cap_refuses_before_attempt(self):
        self.init(limits(per_call=R - 1))
        with Phase2Budget(self.root) as budget:
            with self.assertRaisesRegex(LabError, "PER_CALL_LIMIT"):
                budget.reserve("a" * 64, "b" * 64, now=NOW)
            self.assertEqual(budget.status(now=NOW)["total_attempts"], 0)

    def test_pending_failed_completed_are_never_reissued(self):
        self.init()
        for index, terminal in enumerate((None, "FAILED", "COMPLETED")):
            key = f"{index + 1:064x}"
            at = NOW + timedelta(seconds=index * 2 + 1)
            with Phase2Budget(self.root) as budget:
                self.assertTrue(budget.reserve(key, "b" * 64, now=at)["new"])
                if terminal == "FAILED":
                    budget.finish(key, terminal, now=at)
                elif terminal:
                    completed(budget, key, now=at)
            with Phase2Budget(self.root) as reopened:
                result = reopened.reserve(key, "b" * 64, now=at)
                self.assertFalse(result["new"])
                self.assertEqual(result["record"]["status"], terminal or "RESERVED")
                with self.assertRaisesRegex(LabError, "REQUEST_CONFLICT"):
                    reopened.reserve(key, "c" * 64, now=at)
        with Phase2Budget(self.root) as budget:
            status = budget.status(now=NOW + timedelta(seconds=6))
            self.assertEqual((status["total_attempts"], status["reserved_microusd"]), (3, 3 * R))
            self.assertEqual((status["actual_known_microusd"], status["unknown_cost_attempts"]), (296, 2))

    def test_completion_requires_valid_usage_model_and_hashes(self):
        self.init()
        with Phase2Budget(self.root) as budget:
            budget.reserve("a" * 64, "b" * 64, now=NOW)
            good = dict(response_id="resp_fixture", model=MODEL,
                        usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                        candidate_hash="c" * 64, response_hash="d" * 64, now=NOW)
            cases = [dict(model="another-model"), dict(usage=None), dict(response_id="value-must-not-be-stored"),
                     dict(candidate_hash="../not-a-hash"), dict(response_hash=None),
                     dict(usage={"input_tokens": True, "output_tokens": 1, "total_tokens": 2}),
                     dict(usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 3}),
                     dict(usage={"input_tokens": MAX_INPUT_TOKENS + 1, "output_tokens": 0,
                                 "total_tokens": MAX_INPUT_TOKENS + 1}),
                     dict(usage={"input_tokens": 1, "output_tokens": MAX_OUTPUT_TOKENS + 1,
                                 "total_tokens": MAX_OUTPUT_TOKENS + 2})]
            for extra in cases:
                with self.subTest(fields=list(extra)):
                    with self.assertRaises(LabError):
                        budget.finish("a" * 64, "COMPLETED", **{**good, **extra})
                    self.assertEqual(budget.get("a" * 64)["status"], "RESERVED")
            completed(budget, "a" * 64, now=NOW)
            with self.assertRaisesRegex(LabError, "RESERVATION_STATE"):
                budget.finish("a" * 64, "FAILED", now=NOW)
            with self.assertRaisesRegex(LabError, "RESERVATION_STATE"):
                completed(budget, "a" * 64, now=NOW)

    def test_success_keeps_reservation_and_lifetime_limit_across_days(self):
        self.init(limits(daily=R, total=R, daily_calls=2, total_calls=2))
        with Phase2Budget(self.root) as budget:
            budget.reserve("a" * 64, "b" * 64, now=NOW)
            completed(budget, "a" * 64, now=NOW)
            self.assertEqual(budget.status(now=NOW)["reserved_microusd"], R)
            with self.assertRaisesRegex(LabError, "TOTAL_LIMIT"):
                budget.reserve("c" * 64, "b" * 64, now=NOW + timedelta(days=1))

    def test_daily_boundary_is_utc_and_known_cost_resets_only_daily(self):
        self.init(limits(daily=R, total=3 * R, daily_calls=1, total_calls=3))
        at = NOW + timedelta(hours=23, minutes=58)
        with Phase2Budget(self.root) as budget:
            budget.reserve("a" * 64, "b" * 64, now=at)
            completed(budget, "a" * 64, now=at)
            jst = timezone(timedelta(hours=9))
            with self.assertRaisesRegex(LabError, "DAILY_CALL_LIMIT"):
                budget.reserve("c" * 64, "b" * 64, now=datetime(2026, 11, 7, 8, 59, tzinfo=jst))
            self.assertTrue(budget.reserve("c" * 64, "b" * 64,
                                          now=datetime(2026, 11, 7, 9, 0, tzinfo=jst))["new"])
            status = budget.status(now=NOW + timedelta(days=1))
            self.assertEqual((status["total_attempts"], status["daily_attempts"]), (2, 1))

    def test_unknown_prior_day_cost_cannot_be_freed_by_midnight(self):
        self.init(limits(daily=R, total=4 * R))
        with Phase2Budget(self.root) as budget:
            budget.reserve("a" * 64, "b" * 64, now=NOW)
            budget.finish("a" * 64, "FAILED", now=NOW)
            later = NOW + timedelta(days=1)
            status = budget.status(now=later)
            self.assertEqual((status["daily_attempts"], status["carried_unknown_microusd"]), (0, R))
            with self.assertRaisesRegex(LabError, "DAILY_LIMIT"):
                budget.reserve("c" * 64, "b" * 64, now=later)

    def test_request_finishing_after_midnight_consumes_that_days_exposure(self):
        self.init(limits(daily=R, total=4 * R))
        with Phase2Budget(self.root) as budget:
            budget.reserve("a" * 64, "b" * 64, now=NOW + timedelta(hours=23, minutes=59))
            later = NOW + timedelta(days=1, seconds=20)
            completed(budget, "a" * 64, now=later)
            status = budget.status(now=later)
            self.assertEqual(status["carried_unknown_microusd"], 0)
            self.assertEqual(status["completed_today_carried_microusd"], R)
            with self.assertRaisesRegex(LabError, "DAILY_LIMIT"):
                budget.reserve("c" * 64, "b" * 64, now=later)
            self.assertTrue(budget.reserve("c" * 64, "b" * 64, now=later + timedelta(days=1))["new"])

    def test_clock_rollback_and_price_expiry_block_new_live_reservations(self):
        self.init()
        later = NOW + timedelta(days=1)
        with Phase2Budget(self.root) as budget:
            budget.reserve("a" * 64, "b" * 64, now=later)
            with self.assertRaisesRegex(LabError, "CLOCK_ROLLBACK"):
                budget.reserve("c" * 64, "b" * 64, now=NOW)
            with self.assertRaisesRegex(LabError, "CLOCK_INVALID"):
                budget.reserve("c" * 64, "b" * 64, now=datetime(2026, 11, 7))
            expiry = datetime(2026, 12, 6, tzinfo=timezone.utc)
            with self.assertRaisesRegex(LabError, "PRICE_EXPIRED"):
                budget.reserve("c" * 64, "b" * 64, now=expiry)
            self.assertFalse(budget.status(now=expiry)["price_current"])
            # Pure offline fixtures can still be tested after the quote expires.
            self.assertTrue(budget.reserve("c" * 64, "b" * 64, now=expiry, enforce_pricing=False)["new"])

    def test_atomic_audit_failure_rolls_back_reservation_and_completion(self):
        self.init()
        with Phase2Budget(self.root) as budget:
            with patch("ai_lab.phase2_budget._event", side_effect=sqlite3.OperationalError("secret-value")):
                with self.assertRaisesRegex(LabError, "PHASE2_BUDGET_STORE") as error:
                    budget.reserve("a" * 64, "b" * 64, now=NOW)
            self.assertNotIn("secret-value", str(error.exception))
            self.assertIsNone(budget.get("a" * 64))
            budget.reserve("a" * 64, "b" * 64, now=NOW)
            with patch("ai_lab.phase2_budget._event", side_effect=sqlite3.OperationalError("secret-value")):
                with self.assertRaises(LabError):
                    completed(budget, "a" * 64)
            self.assertEqual(budget.get("a" * 64)["status"], "RESERVED")
            self.assertEqual(budget.status(now=NOW)["reserved_microusd"], R)

    def test_policy_and_reservation_history_are_immutable(self):
        caps = limits(total=R, daily=R, daily_calls=1, total_calls=1)
        self.init(caps)
        caps["total_limit_microusd"] = 999_000_000
        with Phase2Budget(self.root) as budget:
            self.assertEqual(budget.status(now=NOW)["limits"]["total_limit_microusd"], R)
            budget.reserve("a" * 64, "b" * 64, now=NOW)
            for query in ("UPDATE metadata SET policy_json='{}'", "DELETE FROM metadata",
                          "UPDATE reservations SET reserved_microusd=1", "DELETE FROM reservations",
                          "DELETE FROM events", "UPDATE events SET payload_json='{}'"):
                with self.assertRaises(sqlite3.DatabaseError):
                    budget.connection.execute(query)
            budget.limits["total_limit_microusd"] = 999_000_000
            with self.assertRaises(LabError):
                budget.reserve("c" * 64, "b" * 64, now=NOW)
            self.assertTrue(budget.status(now=NOW)["audit_chain_ok"])
        with patch("ai_lab.phase2_budget.PRICE_HASH", "f" * 64):
            with self.assertRaisesRegex(LabError, "POLICY_MISMATCH"):
                Phase2Budget(self.root)

    def test_unrelated_database_is_not_adopted_or_changed(self):
        self.root.mkdir(mode=0o700)
        path = self.root / DB_NAME
        with sqlite3.connect(path) as cn:
            cn.execute("CREATE TABLE balance(amount INTEGER)")
            cn.execute("INSERT INTO balance VALUES(100000)")
        path.chmod(0o600)
        before = path.read_bytes()
        with self.assertRaises(LabError):
            Phase2Budget(self.root)
        with self.assertRaisesRegex(LabError, "ALREADY_EXISTS"):
            self.init()
        self.assertEqual(path.read_bytes(), before)

    def test_symlinks_hardlinks_permissions_and_marker_changes_fail_closed(self):
        self.init()
        linked = self.root.parent / "link"
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(LabError, "STATE_PATH"):
            Phase2Budget(linked)
        with self.assertRaisesRegex(LabError, "STATE_PATH"):
            Phase2Budget(self.root / ".." / "state")
        self.root.chmod(0o755)
        with self.assertRaisesRegex(LabError, "STATE_PERMISSIONS"):
            Phase2Budget(self.root)
        self.root.chmod(0o700)
        path = self.root / DB_NAME
        path.chmod(0o644)
        with self.assertRaisesRegex(LabError, "STATE_FILE"):
            Phase2Budget(self.root)
        path.chmod(0o600)
        hardlink = self.root.parent / "copy.sqlite3"
        os.link(path, hardlink)
        with self.assertRaisesRegex(LabError, "STATE_FILE"):
            Phase2Budget(self.root)
        hardlink.unlink()
        with Phase2Budget(self.root) as budget:
            marker = self.root / MARKER_NAME
            marker.write_text("{}\n")
            with self.assertRaises(LabError):
                budget.reserve("a" * 64, "b" * 64, now=NOW)

    def test_corrupted_database_and_errors_do_not_emit_values(self):
        self.init()
        (self.root / DB_NAME).write_bytes(b"not-sqlite secret-value")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with self.assertRaises(LabError) as error:
                Phase2Budget(self.root)
        self.assertNotIn("secret-value", str(error.exception))
        self.assertEqual(output.getvalue(), "")

    def race(self, *, caps, same=False, root=None):
        root = root or self.root
        self.init(caps, root=root)
        ctx = multiprocessing.get_context("spawn")
        barrier, queue = ctx.Barrier(4), ctx.Queue()
        workers = [ctx.Process(target=race_worker, args=(str(root), i, same, barrier, queue)) for i in range(4)]
        for worker in workers:
            worker.start()
        try:
            result = [queue.get(timeout=20) for _ in workers]
        finally:
            for worker in workers:
                worker.join(timeout=10)
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=2)
                self.assertEqual(worker.exitcode, 0)
            queue.close()
        self.assertNotIn("unexpected", [item[0] for item in result])
        return result

    def test_real_process_daily_money_and_attempt_competition(self):
        cases = [
            (limits(daily=2 * R), "PHASE2_DAILY_LIMIT"),
            (limits(daily=2 * R, total=2 * R), "PHASE2_TOTAL_LIMIT"),
            (limits(daily_calls=2), "PHASE2_DAILY_CALL_LIMIT"),
            (limits(daily_calls=2, total_calls=2), "PHASE2_TOTAL_CALL_LIMIT"),
        ]
        for number, (caps, code) in enumerate(cases):
            root = self.root.parent / f"race-{number}"
            result = self.race(caps=caps, root=root)
            self.assertEqual(result.count(("new", None)), 2)
            self.assertEqual(result.count(("error", code)), 2)
            with Phase2Budget(root) as budget:
                status = budget.status(now=NOW + timedelta(seconds=1))
                self.assertEqual((status["total_attempts"], status["reserved_microusd"]), (2, 2 * R))
                self.assertTrue(status["audit_chain_ok"])

    def test_real_process_duplicate_competition_one_reservation(self):
        result = self.race(caps=limits(daily=R, total=R, daily_calls=1, total_calls=1), same=True)
        self.assertEqual(result.count(("new", None)), 1)
        self.assertEqual(result.count(("existing", None)), 3)

    def test_process_crashes_before_and_after_commit_are_distinct(self):
        ctx = multiprocessing.get_context("spawn")
        for before_commit, code in ((True, 74), (False, 73)):
            root = self.root.parent / f"crash-{code}"
            self.init(root=root)
            worker = ctx.Process(target=crash_worker, args=(str(root), before_commit))
            worker.start()
            worker.join(timeout=20)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=2)
            self.assertEqual(worker.exitcode, code)
            with Phase2Budget(root) as budget:
                row = budget.get("a" * 64)
                if before_commit:
                    self.assertIsNone(row)
                    self.assertEqual(budget.status(now=NOW)["total_attempts"], 0)
                else:
                    self.assertEqual(row["status"], "RESERVED")
                    result = budget.reserve("a" * 64, "b" * 64, now=NOW + timedelta(seconds=1))
                    self.assertFalse(result["new"])
                    self.assertEqual(budget.status(now=NOW + timedelta(seconds=1))["reserved_microusd"], R)


if __name__ == "__main__":
    unittest.main()
