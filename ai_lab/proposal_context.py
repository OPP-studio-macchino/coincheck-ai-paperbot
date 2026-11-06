"""A non-executable, single-candidate handoff shared by two isolated processes.

No evaluator, exchange, database or HTTP imports. Only ``context`` is sent to
the model. Bindings are rederived by the evaluator. Hashes are not signatures.
"""
from __future__ import annotations

import re

from .contracts import (BASELINE, POLICY, POLICY_HASH, LabError, canonical_json,
                        digest, proposal_schema, responses_proposal_schema, strict_json)
from .provider import PROMPT_HASH, PROMPT_VERSION, build_responses_request, validate_context
from .phase2_policy import MODEL, MAX_OUTPUT_TOKENS

ENVELOPE_VERSION = "ai-lab-proposal-request-v1"
MAX_HANDOFF_BYTES = 65536
HASH_PATTERN = re.compile(r"[0-9a-f]{64}")


def valid_hash(value) -> bool:
    return isinstance(value, str) and HASH_PATTERN.fullmatch(value) is not None


def detached(value: dict) -> dict:
    try:
        result = strict_json(canonical_json(value), max_bytes=MAX_HANDOFF_BYTES)
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise LabError("PHASE2_HANDOFF_ENCODING") from None
    if not isinstance(result, dict):
        raise LabError("PHASE2_HANDOFF_OBJECT")
    return result


def _wire_request(context: dict) -> dict:
    request = build_responses_request(context, model=MODEL,
                                      max_output_tokens=MAX_OUTPUT_TOKENS)
    request["service_tier"] = "default"
    request["truncation"] = "disabled"
    return request


def _fixed_binding() -> dict:
    return {"policy_version": POLICY["policy_version"], "policy_hash": POLICY_HASH,
            "baseline_hash": BASELINE.strategy_hash,
            "prompt_version": PROMPT_VERSION, "prompt_hash": PROMPT_HASH,
            "candidate_schema_hash": digest(proposal_schema()),
            "wire_schema_hash": digest(responses_proposal_schema()),
            "model": MODEL, "max_output_tokens": MAX_OUTPUT_TOKENS,
            "initial_cash_jpy": POLICY["initial_cash_jpy"], "max_trials": 1}


def make_envelope(context: dict, *, dataset_hash: str, dataset_metadata: dict,
                  splits: dict, buy_budget_jpy: int, source_hash: str) -> dict:
    """Offline caller first validates the complete frozen CSV."""
    safe = validate_context(context)
    binding = {**_fixed_binding(), "dataset_hash": dataset_hash,
               "dataset_metadata_hash": digest(dataset_metadata),
               "dataset_source": dataset_metadata.get("source"),
               "splits": {key: list(value) for key, value in splits.items()},
               "buy_budget_jpy": buy_budget_jpy, "source_hash": source_hash,
               "context_hash": digest(safe), "request_hash": digest(_wire_request(safe))}
    payload = {"version": ENVELOPE_VERSION, "context": safe, "binding": binding}
    return validate_envelope({**payload, "envelope_hash": digest(payload)})


def validate_envelope(value: dict) -> dict:
    safe = detached(value)
    if set(safe) != {"version", "context", "binding", "envelope_hash"} or safe["version"] != ENVELOPE_VERSION:
        raise LabError("PHASE2_ENVELOPE_KEYS")
    context = validate_context(safe["context"])
    # A future iterative protocol needs explicit WAITING_ARTIFACT states.
    if (context["trial_index"] != 0 or context["development_feedback"] != []
            or context["parent_candidate"] != BASELINE.to_dict()):
        raise LabError("PHASE2_SINGLE_CANDIDATE_REQUIRED")
    binding = safe["binding"]
    fixed = _fixed_binding()
    extra = {"dataset_hash", "dataset_metadata_hash", "dataset_source", "splits",
             "buy_budget_jpy", "source_hash", "context_hash", "request_hash"}
    if not isinstance(binding, dict) or set(binding) != set(fixed) | extra:
        raise LabError("PHASE2_BINDING_KEYS")
    if any(binding[key] != expected or type(binding[key]) is not type(expected)
           for key, expected in fixed.items()):
        raise LabError("PHASE2_BINDING_VERSION")
    if any(not valid_hash(binding[key]) for key in
           ("dataset_hash", "dataset_metadata_hash", "source_hash", "context_hash", "request_hash")):
        raise LabError("PHASE2_BINDING_HASH")
    if binding["dataset_source"] not in ("synthetic", "coincheck", "bitbank", "unknown"):
        raise LabError("PHASE2_DATASET_SOURCE")
    budget = binding["buy_budget_jpy"]
    if type(budget) is not int or not 0 < budget <= POLICY["buy_budget_ceiling_jpy"]:
        raise LabError("BUY_BUDGET_INVALID")
    splits = binding["splits"]
    order = ("warmup", "train", "development", "holdout")
    if not isinstance(splits, dict) or set(splits) != set(order):
        raise LabError("PHASE2_SPLITS_INVALID")
    previous = 0
    for name in order:
        span = splits[name]
        if (not isinstance(span, list) or len(span) != 2
                or any(type(x) is not int for x in span)
                or span[0] != previous or not span[0] < span[1] <= POLICY["max_rows"]
                or (name != "warmup" and (span[1] - span[0]) % 48)):
            raise LabError("PHASE2_SPLITS_INVALID")
        previous = span[1]
    if context["training_summary"]["rows"] != splits["train"][1] - splits["train"][0]:
        raise LabError("PHASE2_TRAINING_ROWS_MISMATCH")
    if binding["context_hash"] != digest(context) or binding["request_hash"] != digest(_wire_request(context)):
        raise LabError("PHASE2_REQUEST_HASH_MISMATCH")
    payload = {"version": safe["version"], "context": context, "binding": binding}
    if not valid_hash(safe["envelope_hash"]) or safe["envelope_hash"] != digest(payload):
        raise LabError("PHASE2_ENVELOPE_HASH_MISMATCH")
    return {**payload, "envelope_hash": safe["envelope_hash"]}


def build_request(envelope: dict) -> dict:
    return _wire_request(validate_envelope(envelope)["context"])
