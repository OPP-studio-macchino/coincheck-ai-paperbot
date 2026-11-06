"""Reviewed, immutable price assumptions for the opt-in generator.

Reservation uses the entire documented context window, not an unverified local
token estimate. This ledger covers one state volume, not other keys, host
compromise, or a changed provider tariff. Tariff updates need reviewed migration.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .contracts import LabError, digest

MODEL = "gpt-4.1-2025-04-14"
PRICE_VERSION = "openai-gpt-4.1-standard-2026-11-06"
PRICE_CHECKED_DATE = "2026-11-06"
PRICE_EXPIRES_DATE = "2026-12-06"  # Exclusive at 00:00 UTC.
MODEL_SHUTDOWN_DATE = "2027-01-14"
MAX_INPUT_TOKENS = 1_047_576
MAX_OUTPUT_TOKENS = 2_048
INPUT_MICROUSD_PER_TOKEN = 2
OUTPUT_MICROUSD_PER_TOKEN = 8
RESERVATION_MICROUSD = (MAX_INPUT_TOKENS * INPUT_MICROUSD_PER_TOKEN
                       + MAX_OUTPUT_TOKENS * OUTPUT_MICROUSD_PER_TOKEN)


def price_record() -> dict:
    return {
        "version": PRICE_VERSION, "model": MODEL, "service_tier": "default",
        "checked_date_utc": PRICE_CHECKED_DATE, "expires_date_utc": PRICE_EXPIRES_DATE,
        "model_shutdown_date_utc": MODEL_SHUTDOWN_DATE,
        "max_input_tokens": MAX_INPUT_TOKENS, "max_output_tokens": MAX_OUTPUT_TOKENS,
        "input_microusd_per_token": INPUT_MICROUSD_PER_TOKEN,
        "output_microusd_per_token": OUTPUT_MICROUSD_PER_TOKEN,
        "cached_input_discount_applied": False,
        "reservation_method": "full_model_context_plus_max_output",
        "source": "https://developers.openai.com/api/docs/models/gpt-4.1",
        "deprecation_source": "https://developers.openai.com/api/docs/deprecations",
    }


PRICE_HASH = digest(price_record())


def ensure_price_current(now: datetime) -> None:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise LabError("PHASE2_CLOCK_INVALID")
    day = now.astimezone(timezone.utc).date().isoformat()
    if not PRICE_CHECKED_DATE <= day < PRICE_EXPIRES_DATE:
        raise LabError("PHASE2_PRICE_EXPIRED")


def normalize_usage(usage: dict) -> dict:
    required = {"input_tokens", "output_tokens", "total_tokens"}
    optional = {"input_tokens_details", "output_tokens_details"}
    if type(usage) is not dict or not required <= set(usage) or set(usage) - required - optional:
        raise LabError("PHASE2_USAGE_INVALID")
    counts = {key: usage[key] for key in required}
    if any(type(value) is not int or value < 0 for value in counts.values()):
        raise LabError("PHASE2_USAGE_INVALID")
    if (counts["input_tokens"] > MAX_INPUT_TOKENS or counts["output_tokens"] > MAX_OUTPUT_TOKENS
            or counts["total_tokens"] != counts["input_tokens"] + counts["output_tokens"]):
        raise LabError("PHASE2_USAGE_INVALID")
    # The public Responses usage schema includes cache_write_tokens in
    # input_tokens_details (and optionally its TTL breakdown). Reject unknown
    # keys and unsafe values, but accept documented additive metadata. Pricing
    # intentionally uses the uncached parent token counts, not these details.
    if "input_tokens_details" in usage:
        details = usage["input_tokens_details"]
        allowed = {"cached_tokens", "cache_write_tokens", "cache_write_tokens_details"}
        if (type(details) is not dict or "cached_tokens" not in details
                or set(details) - allowed):
            raise LabError("PHASE2_USAGE_INVALID")
        for field in ("cached_tokens", "cache_write_tokens"):
            if field in details:
                value = details[field]
                if type(value) is not int or not 0 <= value <= counts["input_tokens"]:
                    raise LabError("PHASE2_USAGE_INVALID")
        if "cache_write_tokens_details" in details:
            breakdown = details["cache_write_tokens_details"]
            if (type(breakdown) is not dict
                    or set(breakdown) - {"ttl_12h_tokens", "ttl_30m_tokens"}):
                raise LabError("PHASE2_USAGE_INVALID")
            for value in breakdown.values():
                if type(value) is not int or not 0 <= value <= counts["input_tokens"]:
                    raise LabError("PHASE2_USAGE_INVALID")
    if "output_tokens_details" in usage:
        details = usage["output_tokens_details"]
        if (type(details) is not dict or set(details) != {"reasoning_tokens"}
                or type(details["reasoning_tokens"]) is not int
                or not 0 <= details["reasoning_tokens"] <= counts["output_tokens"]):
            raise LabError("PHASE2_USAGE_INVALID")
    return counts


def usage_cost(model: str, usage: dict) -> int:
    """Uncached-token USD upper estimate, not provider billing reconciliation."""
    if type(model) is not str or model != MODEL:
        raise LabError("PHASE2_RESPONSE_MODEL")
    counts = normalize_usage(usage)
    return (counts["input_tokens"] * INPUT_MICROUSD_PER_TOKEN
            + counts["output_tokens"] * OUTPUT_MICROUSD_PER_TOKEN)
