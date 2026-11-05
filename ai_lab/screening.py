"""Deterministic screening; never delegated to a language model."""
from __future__ import annotations

import math
from .contracts import LabError, POLICY


def _number(metrics: dict, key: str) -> float:
    value = metrics.get(key)
    if type(value) not in (int,float) or not math.isfinite(value):
        raise LabError("METRICS_INVALID")
    return float(value)


def screen(metrics: dict, baseline: dict, stress: dict, baseline_stress: dict,
           folds: list[dict] | None = None) -> list[str]:
    p = POLICY["screening"]
    reasons = []
    trades = metrics.get("closed_trades")
    if type(trades) is not int or trades < 0:
        raise LabError("METRICS_INVALID")
    if trades < p["minimum_closed_trades"]:
        reasons.append("INSUFFICIENT_CLOSED_TRADES")
    if _number(metrics,"net_profit_jpy") <= p["minimum_net_profit_jpy"]:
        reasons.append("NET_NONPOSITIVE")
    if _number(metrics,"net_profit_jpy") - _number(baseline,"net_profit_jpy") <= p["minimum_baseline_improvement_jpy"]:
        reasons.append("NO_BASELINE_IMPROVEMENT")
    if not 0 <= _number(metrics,"max_drawdown_pct") <= p["max_drawdown_pct"]:
        reasons.append("DRAWDOWN_LIMIT")
    if not 0 <= _number(metrics,"max_single_trade_profit_share") <= p["maximum_single_trade_profit_share"]:
        reasons.append("PROFIT_CONCENTRATION")
    if (_number(stress,"net_profit_jpy") <= 0 or
            _number(stress,"net_profit_jpy") <= _number(baseline_stress,"net_profit_jpy")):
        reasons.append("COST_STRESS_FAILED")
    if folds is not None:
        if len(folds) != 3:
            raise LabError("DEV_FOLDS_INVALID")
        if sum(_number(x,"net_profit_jpy") > 0 for x in folds) < p["minimum_positive_dev_folds"]:
            reasons.append("DEV_FOLD_STABILITY")
    return reasons
