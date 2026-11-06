"""Generator acceptance with fixtures/spies only. No paid API or exchange calls."""
import contextlib
import copy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import signal
import tempfile
import time
import unittest
from unittest.mock import patch

from ai_lab.contracts import BASELINE, POLICY, LabError, canonical_json, digest
from ai_lab.phase2 import generate, main, state_path
from ai_lab.phase2_artifacts import load_candidate
from ai_lab.phase2_budget import Phase2Budget, initialize
from ai_lab.phase2_gateway import HOST, PATH, ResponsesGateway, read_api_key
from ai_lab.phase2_policy import MODEL, RESERVATION_MICROUSD
from ai_lab.proposal_context import build_request, make_envelope


def envelope():
    context = {"training_summary": {"start_utc": "2020-03-01T00:00:00Z",
               "end_utc": "2020-03-02T00:00:00Z", "rows": 96,
               "return_pct": 3.0, "daily_volatility_pct": 2.0, "regime": "uptrend"},
               "development_feedback": [], "parent_candidate": BASELINE.to_dict(),
               "trial_index": 0, "policy_version": POLICY["policy_version"]}
    return make_envelope(context, dataset_hash="a" * 64,
                         dataset_metadata={"source": "synthetic"},
                         splits={"warmup": (0,14400), "train": (14400,14496),
                                 "development": (14496,14592), "holdout": (14592,14688)},
                         buy_budget_jpy=13000, source_hash="c" * 64)


def response():
    return {"id": "resp_unit_spy", "model": MODEL, "status": "completed",
            "error": None, "incomplete_details": None, "service_tier": "default",
            "usage": {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200},
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": canonical_json(BASELINE.to_dict())}]}]}


def limits(calls=2):
    return {"per_call_limit_microusd": RESERVATION_MICROUSD,
            "daily_limit_microusd": RESERVATION_MICROUSD * calls,
            "total_limit_microusd": RESERVATION_MICROUSD * calls,
            "daily_call_limit": calls, "total_call_limit": calls}


class SpyGateway:
    calls = 0
    value = None
    error = None
    def __init__(self, _key): pass
    def send(self, _request):
        type(self).calls += 1
        if type(self).error is not None:
            raise type(self).error
        return copy.deepcopy(type(self).value)


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        from ai_lab.phase2_budget import _utc
        clock = patch("ai_lab.phase2_budget._utc", side_effect=lambda now=None:
                      _utc(now if now is not None else datetime(2026,11,6,3,tzinfo=timezone.utc)))
        clock.start()
        self.addCleanup(clock.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.key = self.root / "key"
        self.canary = "unit_secret_canary_do_not_print"
        self.key.write_text(self.canary + "\n")
        self.key.chmod(0o600)
        self.env = {"AI_LAB_PHASE2_LIVE": "true", "OPENAI_API_KEY_FILE": str(self.key),
                    "DRY_RUN": "true", "LIVE_TRADING_ENABLED": "false", "NEWS_MODE": "mock"}
        SpyGateway.calls = 0
        SpyGateway.value = response()
        SpyGateway.error = None
    def tearDown(self): self.temp.cleanup()
    def init(self, mode="live"):
        initialize(state_path(self.root / "state", mode), limits())
    def call(self, env=None):
        return generate(envelope(), state_root=self.root / "state", mode="live",
                        environ=self.env if env is None else env, gateway_factory=SpyGateway)
    def record(self, mode="live"):
        budget = Phase2Budget(state_path(self.root / "state", mode))
        try: return budget.get(envelope()["binding"]["request_hash"])
        finally: budget.close()

    def test_fixture_needs_no_key_no_network_and_resumes_identical_artifact(self):
        self.init("fixture")
        with patch("socket.socket", side_effect=AssertionError("NETWORK_FORBIDDEN")):
            a = generate(envelope(), state_root=self.root / "state", environ={})
            b = generate(envelope(), state_root=self.root / "state", environ={})
        self.assertEqual(a, b)
        self.assertEqual(a["generation"]["mode"], "fixture")
        self.assertEqual(SpyGateway.calls, 0)
        self.assertEqual(self.record("fixture")["status"], "COMPLETED")

    def test_live_spy_reserves_first_and_restarts_do_not_reissue(self):
        self.init()
        parent = self
        class CheckedGateway(SpyGateway):
            def send(self, request):
                self_record = parent.record()
                parent.assertEqual(self_record["status"], "RESERVED")
                return super().send(request)
        with patch("socket.socket", side_effect=AssertionError("NETWORK_FORBIDDEN")):
            first = generate(envelope(), state_root=self.root / "state", mode="live",
                             environ=self.env, gateway_factory=CheckedGateway)
            again = self.call()
        self.assertEqual(first, again)
        self.assertEqual(CheckedGateway.calls, 1)
        self.assertEqual(SpyGateway.calls, 0)
        self.assertEqual(first["generation"]["reserved_microusd"], RESERVATION_MICROUSD)
        self.assertLess(first["generation"]["actual_microusd"], RESERVATION_MICROUSD)

    def test_keys_and_live_optin_required_before_reservation_or_post(self):
        self.init()
        for changed in ({"AI_LAB_PHASE2_LIVE":"false"}, {"OPENAI_API_KEY_FILE":""},
                        {"DRY_RUN":"false"}, {"LIVE_TRADING_ENABLED":"true"},
                        {"NEWS_MODE":"live"}, {"COINCHECK_API_SECRET":"unit"},
                        {"DB_HOST":"db"}, {"OPENAI_API_KEY":"unit"},
                        {"OPENAI_BASE_URL":"https://other.invalid"}):
            with self.subTest(changed=next(iter(changed))):
                with self.assertRaises(LabError): self.call({**self.env, **changed})
        self.assertEqual(SpyGateway.calls, 0)
        self.assertIsNone(self.record())

    def test_missing_initialized_state_never_posts(self):
        with self.assertRaises(LabError): self.call()
        self.assertEqual(SpyGateway.calls, 0)

    def test_failed_or_incomplete_response_keeps_entire_reservation(self):
        self.init()
        SpyGateway.value["status"] = "incomplete"
        with self.assertRaises(LabError): self.call()
        self.assertEqual(self.record()["status"], "FAILED")
        self.assertEqual(self.record()["reserved_microusd"], RESERVATION_MICROUSD)
        with self.assertRaises(LabError): self.call()
        self.assertEqual(SpyGateway.calls, 1)

    def test_unknown_cost_after_process_interrupt_does_not_retry(self):
        self.init()
        SpyGateway.error = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): self.call()
        self.assertEqual(self.record()["status"], "RESERVED")
        SpyGateway.error = None
        with self.assertRaises(LabError): self.call()
        self.assertEqual(SpyGateway.calls, 1)

    def test_artifact_save_crash_keeps_completed_receipt_and_cannot_repost(self):
        self.init()
        with patch("ai_lab.phase2.save_candidate", side_effect=OSError(self.canary)):
            with self.assertRaises(OSError): self.call()
        self.assertEqual(self.record()["status"], "COMPLETED")
        with self.assertRaises(LabError): self.call()
        self.assertEqual(SpyGateway.calls, 1)

    def test_live_spy_accepts_documented_cache_write_usage(self):
        self.init()
        # Matches the current Responses usage envelope, not a paid API call.
        SpyGateway.value["usage"] = {
            "input_tokens": 620,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": 125,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 745,
        }
        with patch("socket.socket", side_effect=AssertionError("NETWORK_FORBIDDEN")):
            artifact = self.call()
        self.assertEqual(artifact["generation"]["actual_microusd"], 620 * 2 + 125 * 8)
        self.assertEqual(self.record()["status"], "COMPLETED")
        self.assertEqual(self.record()["reserved_microusd"], RESERVATION_MICROUSD)
        self.assertEqual(SpyGateway.calls, 1)

    def test_model_tier_usage_refusal_errors_are_not_candidates(self):
        bad = []
        for key, value in (("model","different-model"),("service_tier","priority"),
                           ("usage",None),("usage",{"input_tokens":True,"output_tokens":1,"total_tokens":2})):
            r=response();r[key]=value;bad.append(r)
        r=response();r["output"][0]["content"]=[{"type":"refusal","refusal":self.canary}];bad.append(r)
        for i, value in enumerate(bad):
            with self.subTest(case=i):
                state = self.root / ("bad"+str(i))
                initialize(state_path(state,"live"), limits())
                SpyGateway.value = value
                with self.assertRaises(LabError) as caught:
                    generate(envelope(),state_root=state,mode="live",environ=self.env,gateway_factory=SpyGateway)
                self.assertNotIn(self.canary,str(caught.exception))
                self.assertFalse((state_path(state,"live")/"artifacts").exists())

    def test_failure_code_is_bounded_and_reservation_is_not_retried(self):
        self.init()
        SpyGateway.error = LabError("PHASE2_HTTP_STATUS_429")
        with self.assertRaisesRegex(LabError, "^PHASE2_GENERATION_FAILED$") as caught:
            self.call()
        self.assertEqual(caught.exception.safe_diagnostic, "PHASE2_HTTP_STATUS_429")
        self.assertEqual(self.record()["status"], "FAILED")
        self.assertEqual(self.record()["reserved_microusd"], RESERVATION_MICROUSD)
        with self.assertRaises(LabError):
            self.call()
        self.assertEqual(SpyGateway.calls, 1)

    def test_secret_in_exception_is_never_printed_as_failure_reason(self):
        for reason in ("PHASE2_HTTP_STATUS_401", self.canary):
            fake_error = LabError("PHASE2_GENERATION_FAILED")
            fake_error.safe_diagnostic = reason
            output = io.StringIO()
            with patch("ai_lab.phase2.read_envelope", return_value=envelope()), \
                 patch("ai_lab.phase2.generate", side_effect=fake_error), \
                 contextlib.redirect_stderr(output):
                self.assertEqual(main(["generate", "--state", str(self.root / "state"),
                                       "--context", str(self.root / "unused.json"),
                                       "--mode", "live"]), 1)
            self.assertIn("PHASE2_COMMAND_FAILED", output.getvalue())
            self.assertNotIn(self.canary, output.getvalue())
            if reason.startswith("PHASE2_HTTP_STATUS_"):
                self.assertIn("cause=" + reason, output.getvalue())
            else:
                self.assertNotIn("cause=", output.getvalue())

    def test_fixture_cannot_use_live_key_and_state_does_not_mix(self):
        self.init("fixture")
        with self.assertRaises(LabError):
            generate(envelope(),state_root=self.root/"state",environ=self.env)
        with self.assertRaises(LabError): self.call()
        self.assertEqual(SpyGateway.calls,0)

    def test_cli_errors_never_echo_arguments_or_exception_values(self):
        for argv in (["generate","--estimated-microusd",self.canary],
                     ["status","--state",str(self.root/self.canary)]):
            output=io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                self.assertEqual(main(argv),1)
            self.assertNotIn(self.canary,output.getvalue())
            self.assertNotIn("Traceback",output.getvalue())

    def test_key_reader_rejects_readable_host_files_symlinks_and_huge_input(self):
        self.assertEqual(read_api_key(self.env),self.canary)
        for mode in (0o644,0o444,0o660):
            self.key.chmod(mode)
            with self.assertRaises(LabError): read_api_key(self.env)
        self.key.chmod(0o600)
        link=self.root/"link";link.symlink_to(self.key)
        with self.assertRaises(LabError): read_api_key({"OPENAI_API_KEY_FILE":str(link)})
        self.key.write_text("x"*100000)
        with self.assertRaises(LabError): read_api_key(self.env)


class HTTPGatewayTests(unittest.TestCase):
    def setUp(self):
        self.request=build_request(envelope())
        self.key="unit_transport_canary_do_not_print"
    def connection(self, body=None, status=200, error=None, delay=0):
        parent=self
        class Connection:
            calls=0;closed=False;instance=None
            def __init__(self,host,**kwargs):
                type(self).instance=self
                parent.assertEqual(host,HOST)
                parent.assertTrue(kwargs["context"].check_hostname)
                parent.assertGreater(kwargs["timeout"],0)
            def request(self,method,path,**kwargs):
                type(self).calls+=1
                parent.assertEqual((method,path),("POST",PATH))
                parent.assertEqual(kwargs["headers"]["Authorization"],"Bearer "+parent.key)
                if error:raise error
            def getresponse(self):
                class Reply:
                    def read(self,limit):
                        if delay:time.sleep(delay)
                        return body if body is not None else canonical_json(response()).encode()
                reply=Reply();reply.status=status;return reply
            def close(self):type(self).closed=True
        return Connection
    def test_exact_tls_post_and_cleanup(self):
        connection=self.connection()
        out=ResponsesGateway(self.key,connection_factory=connection).send(self.request)
        self.assertEqual(out["id"],"resp_unit_spy")
        self.assertEqual(connection.calls,1);self.assertTrue(connection.closed)
    def test_redirect_error_duplicate_json_oversize_and_timeout_are_redacted(self):
        for opts in ({"status":302},{"status":429},{"body":b'{"a":1,"a":2}'},
                     {"body":b'{"x":NaN}'},{"body":b'x'*65537},
                     {"error":TimeoutError(self.key)},{"body":b'[]'}):
            with self.subTest(case=list(opts)):
                connection=self.connection(**opts)
                with self.assertRaises(LabError) as caught:
                    ResponsesGateway(self.key,connection_factory=connection).send(self.request)
                self.assertNotIn(self.key,str(caught.exception))
                self.assertEqual(connection.calls,1);self.assertTrue(connection.closed)
    def test_http_status_diagnostics_never_read_or_show_remote_error(self):
        for status, expected in ((400, "PHASE2_HTTP_STATUS_400"),
                                 (401, "PHASE2_HTTP_STATUS_401"),
                                 (429, "PHASE2_HTTP_STATUS_429"),
                                 (500, "PHASE2_HTTP_STATUS_500"),
                                 (302, "PHASE2_HTTP_STATUS_OTHER")):
            with self.subTest(status=status):
                connection = self.connection(status=status, body=b"secret-canary-body")
                with self.assertRaises(LabError) as caught:
                    ResponsesGateway(self.key, connection_factory=connection).send(self.request)
                self.assertEqual(str(caught.exception), expected)
                self.assertNotIn("secret-canary-body", str(caught.exception))
                self.assertNotIn(self.key, str(caught.exception))
                self.assertEqual(connection.calls, 1)
                self.assertTrue(connection.closed)

    def test_total_deadline_interrupts_slow_read_and_restores_handler(self):
        previous=signal.getsignal(signal.SIGALRM)
        connection=self.connection(delay=.2)
        start=time.monotonic()
        with patch("ai_lab.phase2_gateway.TOTAL_TIMEOUT_SECONDS",.03):
            with self.assertRaises(LabError):
                ResponsesGateway(self.key,connection_factory=connection).send(self.request)
        self.assertLess(time.monotonic()-start,.2)
        self.assertEqual(signal.getsignal(signal.SIGALRM),previous)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.0,0.0))
        self.assertTrue(connection.closed)
    def test_unapproved_request_features_fail_before_connection(self):
        for key,value in (("model","unbounded"),("service_tier","priority"),
                          ("previous_response_id","resp_previous"),("tools",[{"type":"web_search"}])):
            connection=self.connection();request=copy.deepcopy(self.request);request[key]=value
            with self.assertRaises(LabError):
                ResponsesGateway(self.key,connection_factory=connection).send(request)
            self.assertEqual(connection.calls,0)


if __name__ == "__main__": unittest.main()
