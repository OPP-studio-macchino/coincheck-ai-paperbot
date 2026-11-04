"""Fail-closed PAPER-ONLY configuration and dedicated database role identities.

Secrets are read from mounted files, not .env, build args or frontend state.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import os
import re

VERSION = "sec-v0.2"
STRATEGY_VERSION = "btc2h-ema21-55-rsi9-55-w3-htf4h50-200-atr14-sl3-tp12"
MAX_BUY_JPY = Decimal("50000")
DEFAULT_CASH_JPY = Decimal("100000")
EMA_FAST, EMA_SLOW = 21, 55
RSI_PERIOD, RSI_THRESHOLD, RSI_WINDOW = 9, 55, 3
UPPER_EMA_FAST, UPPER_EMA_SLOW = 50, 200
ATR_PERIOD, STOP_ATR, TARGET_ATR = 14, Decimal(3), Decimal(12)
LOCAL_REQUIRED, UPPER_REQUIRED = 200, 300
ROLE_NAMES = {
    "market": "paper_market",
    "news": "paper_news",
    "query": "paper_query",
    "control": "paper_control",
    "migration": "paper_admin",
}
PASSWORD_FILES = {
    "market": "db_market_password",
    "news": "db_news_password",
    "query": "db_query_password",
    "control": "db_control_password",
    "migration": "db_admin_password",
}
CONFIG_FIELDS = (
    EMA_FAST, EMA_SLOW, RSI_PERIOD, RSI_THRESHOLD, RSI_WINDOW,
    UPPER_EMA_FAST, UPPER_EMA_SLOW, ATR_PERIOD, STOP_ATR, TARGET_ATR,
)

def _strict_decimal(key: str, default: str) -> Decimal:
    raw = os.environ.get(key, default)
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError, TypeError):
        raise RuntimeError(f"{key} invalid") from None
    if not value.is_finite() or value != value.to_integral_value():
        raise RuntimeError(f"{key} must be a finite whole JPY amount")
    return value

def read_secret(key: str, *, min_length: int = 40, required: bool = True) -> str:
    """Read only an explicitly designated secret file. Never echo content."""
    candidate = os.environ.get(key, "")
    if not candidate:
        if required:
            raise RuntimeError(f"{key} file path required")
        return ""
    path = Path(candidate)
    if not path.is_absolute() or not path.is_file():
        raise RuntimeError(f"{key} must be an existing absolute file")
    if path.stat().st_size > 4096:
        raise RuntimeError(f"{key} secret too large")
    raw = path.read_text(encoding="utf-8").strip()
    if len(raw) < min_length or any(ch in raw for ch in "\r\n\x00"):
        raise RuntimeError(f"{key} secret invalid")
    return raw

@dataclass(frozen=True)
class Settings:
    buy_jpy: Decimal = MAX_BUY_JPY
    initial_jpy: Decimal = DEFAULT_CASH_JPY
    news_mode: str = "mock"
    fee_bps: Decimal = Decimal("0")
    git_sha: str = "unknown"
    @classmethod
    def load(cls) -> "Settings":
        if os.environ.get("DRY_RUN", "true").lower() != "true":
            raise RuntimeError("DRY_RUN=true mandatory; no real trading adapter")
        if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
            raise RuntimeError("LIVE_TRADING_ENABLED=false mandatory")
        if any(os.environ.get(k) for k in (
            "COINCHECK_API_KEY", "COINCHECK_API_SECRET",
            "COINCHECK_ACCESS_KEY", "COINCHECK_SECRET_KEY"
        )):
            raise RuntimeError("Coincheck private credentials forbidden")
        mode = os.environ.get("NEWS_MODE", "mock")
        if mode not in ("mock", "live"):
            raise RuntimeError("NEWS_MODE must be mock or live")
        # Explicit per-deployment opt-in; the isolated test environment never sets this.
        if mode == "live" and os.environ.get("ALLOW_BILLABLE_NEWS", "") != "I_ACCEPT_BILLING":
            raise RuntimeError("Billable news mode not explicitly approved")
        budget = _strict_decimal("BUY_BUDGET_JPY", "50000")
        initial = _strict_decimal("INITIAL_CASH_JPY", "100000")
        fee = _strict_decimal("PAPER_FEE_BPS", "0")
        if not (Decimal("0") < budget <= MAX_BUY_JPY and initial >= budget):
            raise RuntimeError("Invalid buy budget / initial capital")
        # schema.sql seeds new paper portfolios at 100,000 JPY each.
        # Different seed funding requires a separate, reviewed migration;
        # never claim another value is already applied to existing balances.
        if initial != DEFAULT_CASH_JPY:
            raise RuntimeError("INITIAL_CASH_JPY is fixed to schema seed 100000")
        if fee < 0 or fee > 100:
            raise RuntimeError("Unsupported paper fee bps")
        sha = os.environ.get("APP_GIT_SHA", "unknown")
        if sha != "unknown" and not re.fullmatch("[0-9a-f]{40}", sha):
            raise RuntimeError("APP_GIT_SHA must be a full 40-char SHA")
        return cls(budget, initial, mode, fee, sha)

def db_connect(role: str, *, autocommit: bool = True):
    if role not in ROLE_NAMES:
        raise RuntimeError("Unknown DB role")
    # No DATABASE_URL fallback: runtime must not accept the schema-owner account.
    if os.environ.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL not supported by role-separated runtime")
    import psycopg
    file_env = "DB_" + role.upper() + "_PASSWORD_FILE"
    password = read_secret(file_env, min_length=32)
    host = os.environ.get("DB_HOST", "db")
    name = os.environ.get("POSTGRES_DB", "paperbot")
    if host not in ("db", "127.0.0.1", "localhost") or not re.fullmatch(r"[a-zA-Z0-9_]{1,40}", name):
        raise RuntimeError("Invalid database host / name")
    return psycopg.connect(host=host, dbname=name, user=ROLE_NAMES[role],
                           password=password, connect_timeout=8,
                           autocommit=autocommit, application_name="paper_"+role)
