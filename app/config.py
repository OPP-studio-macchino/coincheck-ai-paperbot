"""Fail-closed settings. This repository deliberately has no live-order adapter."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
import os


@dataclass(frozen=True)
class Settings:
    database_url: str
    buy_jpy: Decimal = Decimal('50000')
    initial_jpy: Decimal = Decimal('100000')
    news_mode: str = 'mock'

    @classmethod
    def load(cls) -> 'Settings':
        if os.getenv('DRY_RUN', 'true').lower() != 'true':
            raise RuntimeError('DRY_RUN=true mandatory; real trading is not implemented')
        if os.getenv('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
            raise RuntimeError('LIVE_TRADING_ENABLED must be false')
        if any(os.getenv(key) for key in ('COINCHECK_API_KEY', 'COINCHECK_API_SECRET')):
            raise RuntimeError('No exchange private API keys permitted in paper-only runtime')
        mode = os.getenv('NEWS_MODE', 'mock')
        if mode not in ('mock', 'live'):
            raise RuntimeError('NEWS_MODE must be mock or live')
        budget = Decimal(os.getenv('BUY_BUDGET_JPY', '50000'))
        initial = Decimal(os.getenv('INITIAL_CASH_JPY', '100000'))
        if not Decimal(0) < budget <= Decimal(50000) or initial < budget:
            raise RuntimeError('Invalid simulated cash limit')
        url = os.getenv('DATABASE_URL', '')
        if not url.startswith('postgresql://'):
            raise RuntimeError('DATABASE_URL must be PostgreSQL')
        return cls(url, budget, initial, mode)
