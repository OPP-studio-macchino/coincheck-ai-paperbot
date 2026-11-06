"""Explicit, bounded generation CLI. No imports of Bot or evaluation code."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from .contracts import LabError, canonical_json, digest, validate_candidate
from .phase2_artifacts import load_candidate, read_envelope, read_json_file, save_candidate
from .phase2_budget import Phase2Budget, initialize
from .phase2_gateway import ResponsesGateway, read_api_key, validate_request
from .phase2_policy import MODEL, normalize_usage
from .proposal_context import build_request, validate_envelope
from .provider import FixtureProvider, parse_responses_output


# Fixed, reviewed diagnostics only. Exception text, provider error bodies and
# arbitrary gateway strings must never enter the CLI output or audit trail.
SAFE_FAILURE_DIAGNOSTICS = frozenset({
    *(f"PHASE2_HTTP_STATUS_{x}" for x in
      (400, 401, 402, 403, 404, 408, 409, 422, 429, 500, 502, 503, 504)),
    "PHASE2_HTTP_STATUS_OTHER", "PHASE2_HTTP_FAILED",
    "PHASE2_HTTP_INVALID", "RESPONSE_INCOMPLETE", "RESPONSE_ERROR",
    "RESPONSE_OUTPUT_INVALID", "RESPONSE_MESSAGE_INVALID",
    "RESPONSE_TOOLS_OR_UNKNOWN_OUTPUT", "RESPONSE_AMBIGUOUS_OUTPUT",
    "RESPONSE_CONTENT_INVALID", "RESPONSE_SIZE_OR_TYPE",
    "RESPONSE_ENCODING", "MODEL_REFUSAL", "PHASE2_RESPONSE_MODEL_OR_TIER",
    "PHASE2_RESPONSE_USAGE", "PHASE2_USAGE_INVALID",
    "PHASE2_STAGE_GATEWAY", "PHASE2_STAGE_PARSE",
    "PHASE2_STAGE_METADATA",
})


def validate_environment(mode: str, environ=None) -> None:
    env = os.environ if environ is None else environ
    if mode not in ("fixture", "live"):
        raise LabError("PHASE2_MODE_INVALID")
    if (env.get("DRY_RUN", "true") != "true"
            or env.get("LIVE_TRADING_ENABLED", "false") != "false"
            or env.get("NEWS_MODE", "mock") != "mock"):
        raise LabError("PHASE2_PAPER_ONLY")
    for name, value in env.items():
        if not value:
            continue
        upper = name.upper()
        if (upper.startswith(("COINCHECK_", "POSTGRES_", "DB_"))
                or upper in {"DATABASE_URL", "ADMIN_TOKEN", "ADMIN_TOKEN_FILE",
                             "VIEW_TOKEN", "VIEW_TOKEN_FILE", "OPENAI_API_KEY",
                             "OPENAI_BASE_URL"}):
            raise LabError("PHASE2_UNEXPECTED_CREDENTIAL_OR_DESTINATION")
    if mode == "live" and env.get("AI_LAB_PHASE2_LIVE") != "true":
        raise LabError("PHASE2_OPT_IN_REQUIRED")
    if mode == "fixture" and (env.get("OPENAI_API_KEY_FILE")
                              or env.get("AI_LAB_PHASE2_LIVE") == "true"):
        raise LabError("PHASE2_FIXTURE_CREDENTIALS_FORBIDDEN")


def state_path(root: Path, mode: str) -> Path:
    if mode not in ("fixture", "live"):
        raise LabError("PHASE2_MODE_INVALID")
    path = Path(root).absolute()
    if ".." in path.parts or any(part.is_symlink() for part in [path, *path.parents]):
        raise LabError("PHASE2_STATE_PATH")
    return path / mode


def _fixture_response(envelope: dict, request_hash: str) -> dict:
    candidate = FixtureProvider().propose(envelope["context"], 0)
    return {"id": "resp_fixture_" + request_hash[:24], "model": MODEL,
            "status": "completed", "error": None, "incomplete_details": None,
            "service_tier": "default",
            "usage": {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200},
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": canonical_json(candidate)}]}]}


def generate(envelope: dict, *, state_root: Path, mode: str = "fixture",
             environ=None, gateway_factory=ResponsesGateway) -> dict:
    """Return a validated artifact, replay only a completed matching local receipt.

    ``gateway_factory`` is an in-process testing seam, never a CLI setting.
    Unknown/failed requests are never automatically repeated, even after a crash.
    """
    validate_environment(mode, environ)
    safe = validate_envelope(envelope)
    request = build_request(safe)
    validate_request(request)
    request_hash = digest(request)
    context_hash = digest(safe["context"])
    key = read_api_key(environ) if mode == "live" else None
    path = state_path(state_root, mode)
    budget = Phase2Budget(path)
    try:
        reservation = budget.reserve(request_hash, context_hash, enforce_pricing=(mode == "live"))
        if not reservation["new"]:
            receipt = reservation["record"]
            if receipt["status"] != "COMPLETED":
                raise LabError("PHASE2_PREVIOUS_ATTEMPT_UNCERTAIN_OR_FAILED")
            artifact = load_candidate(path / "artifacts" / (request_hash + ".json"),
                                      expected_envelope=safe)
            expected_generation = {"mode": "fixture" if mode == "fixture" else "openai_responses",
                                   **receipt}
            if artifact["generation"] != expected_generation:
                raise LabError("PHASE2_RECEIPT_MISMATCH")
            return artifact
        stage = "GATEWAY"
        try:
            response = (_fixture_response(safe, request_hash) if mode == "fixture"
                        else gateway_factory(key).send(request))
            stage = "PARSE"
            candidate = parse_responses_output(response)
            stage = "METADATA"
            if response.get("model") != MODEL or response.get("service_tier") != "default":
                raise LabError("PHASE2_RESPONSE_MODEL_OR_TIER")
            usage = normalize_usage(response.get("usage"))
            if usage["input_tokens"] <= 0 or usage["output_tokens"] <= 0:
                raise LabError("PHASE2_RESPONSE_USAGE")
            budget.finish(request_hash, "COMPLETED", response_id=response.get("id"),
                          model=response["model"], usage=usage,
                          candidate_hash=validate_candidate(candidate).strategy_hash,
                          response_hash=digest(response))
        except Exception as exc:
            # Capture only a fixed error category. Do not log exception strings,
            # credentials, response bodies, input paths, headers or token data.
            diagnostic = "PHASE2_STAGE_" + stage
            if type(exc) is LabError and len(exc.args) == 1:
                if type(exc.args[0]) is str and exc.args[0] in SAFE_FAILURE_DIAGNOSTICS:
                    diagnostic = exc.args[0]
            # Never release a reservation: even an HTTP error may be charged.
            try:
                if budget.get(request_hash)["status"] == "RESERVED":
                    budget.finish(request_hash, "FAILED")
            except Exception:
                pass  # Original reservation remains fail-closed on a DB failure.
            failure = LabError("PHASE2_GENERATION_FAILED")
            failure.safe_diagnostic = diagnostic
            raise failure from None
        receipt = budget.get(request_hash)
        generation = {"mode": "fixture" if mode == "fixture" else "openai_responses", **receipt}
        # COMPLETE first. A crash before durable artifact save consumes the attempt
        # and requires operator investigation; it must not trigger another POST.
        target = save_candidate(path / "artifacts", safe, candidate, generation=generation)
        return load_candidate(target, expected_envelope=safe)
    finally:
        budget.close()


class SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        raise LabError("PHASE2_ARGUMENTS_INVALID")


def main(argv=None) -> int:
    parser = SafeParser(description="Separate research generator. Default fixture; explicit live opt-in.")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=SafeParser)
    for command in ("init", "status", "generate"):
        current = sub.add_parser(command)
        current.add_argument("--state", type=Path, default=Path("/var/lib/ai-generator"))
        current.add_argument("--mode", choices=("fixture", "live"), default="fixture")
        if command == "init":
            current.add_argument("--limits", type=Path, required=True)
        if command == "generate":
            current.add_argument("--context", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        if args.command == "init":
            # This sets a finite ledger policy, not permission to make an API call.
            result = initialize(state_path(args.state, args.mode), read_json_file(args.limits))
        elif args.command == "status":
            budget = Phase2Budget(state_path(args.state, args.mode))
            try:
                result = budget.status()
            finally:
                budget.close()
        else:
            artifact = generate(read_envelope(args.context), state_root=args.state, mode=args.mode)
            result = {"artifact_hash": artifact["artifact_hash"],
                      "candidate_hash": artifact["generation"]["candidate_hash"],
                      "request_hash": artifact["generation"]["request_hash"],
                      "generation_mode": artifact["generation"]["mode"],
                      "artifact_path": str(state_path(args.state, args.mode) / "artifacts" /
                                           (artifact["generation"]["request_hash"] + ".json"))}
        print(canonical_json({"mode": args.mode, "result": result}))
        return 0
    except Exception as exc:
        # Print only an internal allowlisted diagnostic. No exception string,
        # response, request, path or key may be echoed.
        diagnostic = getattr(exc, "safe_diagnostic", None)
        if type(diagnostic) is str and diagnostic in SAFE_FAILURE_DIAGNOSTICS:
            print("AI_LAB_ERROR PHASE2_COMMAND_FAILED cause=" + diagnostic,
                  file=sys.stderr)
        else:
            print("AI_LAB_ERROR PHASE2_COMMAND_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
