"""Single machine-readable research policy and a non-executable candidate DSL."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

POLICY_PATH = Path(__file__).with_name("policy.json")
POLICY = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
POLICY_HASH = hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest()


class LabError(ValueError):
    """A bounded error code; never include untrusted text or secret values."""


class ResearchDeadline(BaseException):
    """Supervisor stop: must not be swallowed as an invalid candidate/API error."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def strict_json(raw: str | bytes, *, max_bytes: int = 16384) -> Any:
    if not isinstance(raw, (str, bytes)) or len(raw.encode() if isinstance(raw, str) else raw) > max_bytes:
        raise LabError("JSON_SIZE_OR_TYPE")

    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise LabError("JSON_DUPLICATE_KEY")
            out[key] = value
        return out

    def invalid_constant(_):
        raise LabError("JSON_NONFINITE")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except LabError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise LabError("JSON_INVALID") from None


@dataclass(frozen=True)
class Candidate:
    schema_version: str
    name: str
    timeframe_minutes: int
    ema_fast: int
    ema_slow: int
    rsi_period: int
    rsi_threshold: int
    rsi_window: int
    atr_period: int
    stop_atr: float
    target_atr: float
    entry_filters: tuple[str, ...]
    volatility_cap_pct: float

    def to_dict(self) -> dict:
        result = asdict(self)
        result["entry_filters"] = list(self.entry_filters)
        return result

    @property
    def strategy_hash(self) -> str:
        # Labels are not strategy identity; renaming cannot evade deduplication.
        value = self.to_dict()
        value.pop("name")
        if "volatility_cap" not in self.entry_filters:
            value.pop("volatility_cap_pct")
        return digest(value)


def proposal_schema() -> dict:
    p = POLICY
    props = {
        "schema_version": {"type": "string", "enum": [p["schema_version"]]},
        "name": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,47}$"},
        "timeframe_minutes": {"type": "integer", "enum": p["timeframes_minutes"]},
        "ema_fast": {"type": "integer", "minimum": p["ema_fast_range"][0], "maximum": p["ema_fast_range"][1]},
        "ema_slow": {"type": "integer", "minimum": p["ema_slow_range"][0], "maximum": p["ema_slow_range"][1]},
        "rsi_period": {"type": "integer", "enum": p["rsi_periods"]},
        "rsi_threshold": {"type": "integer", "enum": p["rsi_thresholds"]},
        "rsi_window": {"type": "integer", "enum": p["rsi_windows"]},
        "atr_period": {"type": "integer", "enum": p["atr_periods"]},
        "stop_atr": {"type": "number", "enum": p["stop_atr_values"]},
        "target_atr": {"type": "number", "enum": p["target_atr_values"]},
        "entry_filters": {"type": "array", "items": {"type": "string", "enum": p["allowed_filters"]}, "minItems": 3, "maxItems": 5},
        "volatility_cap_pct": {"type": "number", "minimum": p["volatility_cap_pct_range"][0], "maximum": p["volatility_cap_pct_range"][1]},
    }
    return {"type": "object", "properties": props,
            "required": list(props), "additionalProperties": False}


def validate_candidate(raw: dict | Candidate) -> Candidate:
    if isinstance(raw, Candidate):
        raw = raw.to_dict()
    schema = proposal_schema()
    if not isinstance(raw, dict) or set(raw) != set(schema["required"]):
        raise LabError("CANDIDATE_KEYS")
    try:
        if len(canonical_json(raw).encode()) > POLICY["max_json_bytes"]:
            raise LabError("CANDIDATE_SIZE")
    except LabError:
        raise
    except (TypeError, ValueError, RecursionError):
        raise LabError("CANDIDATE_ENCODING") from None
    for field, spec in schema["properties"].items():
        value = raw[field]
        kind = spec["type"]
        if kind == "integer" and type(value) is not int:
            raise LabError("CANDIDATE_INTEGER")
        if kind == "number":
            try:
                valid = type(value) in (int, float) and math.isfinite(value)
            except OverflowError:
                valid = False
            if not valid:
                raise LabError("CANDIDATE_NUMBER")
        if kind == "string" and type(value) is not str:
            raise LabError("CANDIDATE_STRING")
        if "enum" in spec and value not in spec["enum"]:
            raise LabError("CANDIDATE_ENUM")
        if "minimum" in spec and not spec["minimum"] <= value <= spec["maximum"]:
            raise LabError("CANDIDATE_RANGE")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", raw["name"]):
        raise LabError("CANDIDATE_NAME")
    if raw["ema_fast"] >= raw["ema_slow"]:
        raise LabError("EMA_ORDER")
    filters = raw["entry_filters"]
    if not isinstance(filters, (list, tuple)) or not 3 <= len(filters) <= 5:
        raise LabError("CANDIDATE_FILTERS")
    if any(type(x) is not str or x not in POLICY["allowed_filters"] for x in filters):
        raise LabError("CANDIDATE_FILTERS")
    if len(set(filters)) != len(filters) or not set(POLICY["required_filters"]) <= set(filters):
        raise LabError("CANDIDATE_REQUIRED_FILTERS")
    value = dict(raw)
    # Filter order has no semantic meaning. Canonicalize it before hashing.
    value["entry_filters"] = tuple(x for x in POLICY["allowed_filters"] if x in filters)
    value["stop_atr"] = float(value["stop_atr"])
    value["target_atr"] = float(value["target_atr"])
    value["volatility_cap_pct"] = float(value["volatility_cap_pct"])
    return Candidate(**value)


BASELINE = validate_candidate(POLICY["baseline"])


def validate_environment(environ: dict) -> None:
    for key, expected in (("DRY_RUN", "true"), ("LIVE_TRADING_ENABLED", "false"), ("NEWS_MODE", "mock")):
        if str(environ.get(key, expected)).lower() != expected:
            raise LabError("RESEARCH_FLAGS_REQUIRED")
    if environ.get("AI_LAB_PROVIDER", "fixture") != "fixture":
        raise LabError("LIVE_PROVIDER_DISABLED")
    # Do not inspect values; this process needs no credentials of any kind.
    if any(environ.get(k) for k in environ if k.startswith("COINCHECK_") and ("KEY" in k or "SECRET" in k)):
        raise LabError("PRIVATE_EXCHANGE_CREDENTIALS_FORBIDDEN")
    if any(environ.get(k) for k in ("OPENAI_API_KEY", "OPENAI_API_KEY_FILE", "DATABASE_URL")):
        raise LabError("RESEARCH_CREDENTIALS_FORBIDDEN")
