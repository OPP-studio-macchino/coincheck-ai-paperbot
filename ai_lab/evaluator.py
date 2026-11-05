"""Causal, offline research evaluation on a five-minute execution clock.

The immutable production strategy lives in app.strategy.  Only Candidate inputs
vary here.  Prices are historical OHLC assumptions, never executable quotes.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from math import isfinite

from app.strategy import Candle, atr, ema, rsi
from ai_lab.contracts import BASELINE, POLICY, Candidate, LabError, validate_candidate
from ai_lab.dataset import Dataset, chronological_split, validate_dataset

UTC = timezone.utc
BASE_SECONDS = 300


@dataclass(frozen=True)
class Decision:
    fill_index: int
    signal_end: datetime
    valid: bool
    atr: float | None
    cross_id: str | None
    snapshot: dict
    reason: str = "conditions_not_met"

    def to_dict(self) -> dict:
        value = asdict(self)
        value["signal_end"] = self.signal_end.isoformat()
        return value


@dataclass(frozen=True)
class Metrics:
    initial_cash_jpy: float
    net_profit_jpy: float
    net_return_pct: float
    ending_equity_jpy: float
    mark_to_market_equity_jpy: float
    liquidated_end_equity_jpy: float
    realized_pnl_jpy: float
    unrealized_pnl_jpy: float
    max_drawdown_pct: float
    profit_factor: float | None
    closed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    gross_profit_jpy: float
    gross_loss_jpy: float
    max_single_trade_profit_share: float
    exposure_pct: float
    max_holding_hours: float
    total_fees_jpy: float
    execution_cost_jpy: float
    open_position_btc: float
    ambiguous_exit_bars: int
    rejected_entries: int
    forced_end_exits: int
    total_exit_records: int
    period_end_pnl_jpy: float
    rejected_exits: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Evaluation:
    candidate_hash: str
    dataset_hash: str
    start_index: int
    end_index: int
    start_utc: str
    end_utc: str
    cost_multiplier: float
    metrics: Metrics
    trades: tuple[dict, ...]
    assumptions: dict

    def to_dict(self) -> dict:
        value = asdict(self)
        value["metrics"] = self.metrics.to_dict()
        value["trades"] = list(self.trades)
        return value


def _aggregate(candles: tuple[Candle, ...], minutes: int) -> tuple[list[Candle], list[int]]:
    """Return complete UTC bins and their first subsequent five-minute index."""
    width = minutes // 5
    seconds = minutes * 60
    bars: list[Candle] = []
    ends: list[int] = []
    i = 0
    while i < len(candles):
        stamp = int(candles[i].start.timestamp())
        if stamp % seconds:
            i += 1
            continue
        group = candles[i:i + width]
        if len(group) != width:
            break
        if int((group[-1].start - group[0].start).total_seconds()) != (width - 1) * BASE_SECONDS:
            raise LabError("history_gap")
        bars.append(Candle(group[0].start, group[0].open,
                           max(c.high for c in group), min(c.low for c in group),
                           group[-1].close, sum(c.volume for c in group)))
        ends.append(i + width)
        i += width
    return bars, ends


def build_signals(dataset: Dataset, candidate: Candidate = BASELINE, *,
                  end_index: int | None = None) -> list[Decision]:
    """Evaluate only the prefix [0,end_index); the holdout is not inspected.

    A decision at signal_end can be filled only at the open of the next five
    minute bar (whose start equals signal_end).  End-of-prefix decisions are
    returned, but have no available fill bar inside that prefix.
    """
    c = validate_candidate(candidate)
    end = len(dataset.candles) if end_index is None else end_index
    if type(end) is not int or not 0 < end <= len(dataset.candles):
        raise LabError("invalid_evaluation_bounds")
    prefix = Dataset(tuple(dataset.candles[:end]), dataset.sha256, dataset.source,
                     tuple(dataset.available_at[:end]) if dataset.available_at else ())
    validate_dataset(prefix)
    local, local_ends = _aggregate(prefix.candles, c.timeframe_minutes)
    higher, higher_ends = _aggregate(prefix.candles, 240)
    close = [bar.close for bar in local]
    fast, slow = ema(close, c.ema_fast), ema(close, c.ema_slow)
    rv, av = rsi(close, c.rsi_period), atr(local, c.atr_period)
    hclose = [bar.close for bar in higher]
    execution = POLICY["execution"]
    history = execution["indicator_history_bars"]
    hfast, hslow = ema(hclose, execution["higher_ema_fast"]), ema(hclose, execution["higher_ema_slow"])
    # The market worker supplies at most 450 bars. Seed each moving window just
    # as app.strategy.evaluate does; full-history EMA seeds are not equivalent.
    for hidx in range(history, len(higher)):
        window = hclose[hidx + 1 - history:hidx + 1]
        hfast[hidx] = ema(window, execution["higher_ema_fast"])[-1]
        hslow[hidx] = ema(window, execution["higher_ema_slow"])[-1]
    # Prefix maximum preserves the causal availability requirement even for a
    # delayed input earlier in the indicator's history.
    latest_available: list[datetime] = []
    latest = None
    for i, bar in enumerate(prefix.candles):
        available = (prefix.available_at[i] if prefix.available_at else
                     bar.start + timedelta(minutes=5))
        latest = available if latest is None else max(latest, available)
        latest_available.append(latest)
    out: list[Decision] = []
    for i, bar in enumerate(local):
        fill_index = local_ends[i]
        end_time = bar.start + timedelta(minutes=c.timeframe_minutes)
        h = bisect_right(higher_ends, fill_index) - 1
        warm = (min(i + 1, history) >= execution["local_warmup_bars"] and
                min(h + 1, history) >= execution["higher_warmup_bars"])
        if i >= history:
            window_bars = local[i + 1 - history:i + 1]
            window_close = close[i + 1 - history:i + 1]
            wf, ws = ema(window_close, c.ema_fast), ema(window_close, c.ema_slow)
            wr, wa = rsi(window_close, c.rsi_period), atr(window_bars, c.atr_period)
            at = history - 1
        else:
            wf, ws, wr, wa, at = fast, slow, rv, av, i
        recent = next((i - j for j in range(c.rsi_window + 1)
                       if at - j > 0 and wr[at - j] is not None
                       and wr[at - j - 1] is not None
                       and wr[at - j - 1] <= c.rsi_threshold < wr[at - j]), None)
        local_trend = close[i] > wf[at] > ws[at]
        rising = at >= 3 and ws[at] > ws[at - 3]
        higher_trend = h >= 0 and hclose[h] > hfast[h] > hslow[h]
        window_ok = recent is not None and wr[at] is not None and wr[at] >= c.rsi_threshold
        cap_ok = wa[at] is not None and wa[at] / close[i] * 100 <= c.volatility_cap_pct
        checks = {"local_trend": local_trend, "slow_rising": rising,
                  "higher_trend": higher_trend, "rsi_cross": window_ok,
                  "volatility_cap": cap_ok}
        available = latest_available[fill_index - 1] <= end_time
        valid = warm and available and wa[at] is not None and all(checks[k] for k in c.entry_filters)
        # Keep production baseline keys and rounding identical for audit parity;
        # generic keys give candidate parameters their actual meaning.
        snap = {"bar_start_utc": bar.start.isoformat(),
                "rsi9": round(wr[at], 5) if wr[at] is not None else None,
                "ema21": round(wf[at], 4), "ema55": round(ws[at], 4),
                "higher_ema50": round(hfast[h], 4) if h >= 0 else None,
                "higher_ema200": round(hslow[h], 4) if h >= 0 else None,
                "atr14": round(wa[at], 4) if wa[at] is not None else None,
                "rsi_window_age": i - recent if recent is not None else None,
                "trend_ok": local_trend and rising,
                "higher_ok": higher_trend, "rsi_window_ok": window_ok,
                "ema_fast_value": wf[at], "ema_slow_value": ws[at],
                "rsi_value": wr[at], "atr_value": wa[at],
                "higher_bar_end_utc": (higher[h].start + timedelta(hours=4)).isoformat() if h >= 0 else None,
                "data_available": available, "warmup_complete": warm,
                "filter_checks": checks}
        reason = ("warmup_incomplete" if not warm else
                  "data_unavailable_at_signal" if not available else
                  "signal" if valid else "conditions_not_met")
        cross = local[recent].start.isoformat() if recent is not None else None
        out.append(Decision(fill_index, end_time, bool(valid), wa[at], cross, snap, reason))
    return out


def _finite_number(value: float, code: str) -> float:
    try:
        valid = type(value) in (int, float) and isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise LabError(code)
    return float(value)


def simulate(dataset: Dataset, candidate: Candidate, decisions: list[Decision], *,
             start_index: int, end_index: int, cost_multiplier: float = 1.0,
             buy_budget_jpy: float = 50_000.0) -> Evaluation:
    """Replay validated decisions. Public for deterministic execution tests.

    Segment accounting starts flat and ends with a separately labelled synthetic
    liquidation.  Raw close mark-to-market equity is reported before that sale.
    """
    c = validate_candidate(candidate)
    if (type(start_index) is not int or type(end_index) is not int or
            not 0 <= start_index < end_index <= len(dataset.candles)):
        raise LabError("invalid_evaluation_bounds")
    validate_dataset(Dataset(tuple(dataset.candles[:end_index]), dataset.sha256, dataset.source,
                             tuple(dataset.available_at[:end_index]) if dataset.available_at else ()))
    mult = _finite_number(cost_multiplier, "invalid_cost_multiplier")
    budget = _finite_number(buy_budget_jpy, "invalid_buy_budget")
    if not 0 <= mult <= 100 or not 0 < budget <= POLICY["buy_budget_ceiling_jpy"]:
        raise LabError("invalid_execution_assumptions")
    ex = POLICY["execution"]
    impact = (ex["spread_bps_round_trip"] / 2 + ex["slippage_bps_per_side"]) * mult / 10_000
    fee_rate = ex["fee_bps_per_side"] * mult / 10_000
    if impact >= 1 or fee_rate >= 1:
        raise LabError("invalid_execution_assumptions")
    by_index: dict[int, Decision] = {}
    for decision in decisions:
        if type(decision.fill_index) is not int or decision.fill_index in by_index:
            raise LabError("duplicate_or_invalid_signal")
        if decision.fill_index < 0 or decision.fill_index > end_index:
            raise LabError("signal_outside_prefix")
        if decision.fill_index < end_index and dataset.candles[decision.fill_index].start != decision.signal_end:
            raise LabError("signal_fill_not_next_open")
        if decision.valid and (decision.atr is None or not isfinite(decision.atr) or decision.atr <= 0 or not decision.cross_id):
            raise LabError("invalid_entry_signal")
        by_index[decision.fill_index] = decision
    initial = float(POLICY["initial_cash_jpy"])
    cash = initial
    position = None
    used_crosses: set[str] = set()
    trades: list[dict] = []
    fees = execution_cost = 0.0
    peak = initial
    drawdown = 0.0
    held_bars = ambiguous = rejected = forced = rejected_exits = 0
    max_hold = 0.0

    def close_position(reference: float, timestamp: datetime, reason: str, *, forced_exit: bool = False):
        nonlocal cash, position, fees, execution_cost, max_hold, forced, rejected_exits
        assert position is not None
        fill = reference * (1 - impact)
        proceeds = position["quantity"] * fill
        if not forced_exit and (position["quantity"] < float(POLICY["min_btc"]) or
                                proceeds < POLICY["min_notional_jpy"]):
            rejected_exits += 1
            return
        sell_fee = proceeds * fee_rate
        cash += proceeds - sell_fee
        fees += sell_fee
        execution_cost += position["quantity"] * (reference - fill) + sell_fee
        holding = (timestamp - position["entry_time"]).total_seconds() / 3600
        max_hold = max(max_hold, holding)
        pnl = proceeds - sell_fee - position["outlay"]
        trades.append({"entry_time": position["entry_time"].isoformat(),
                       "signal_end": position["signal_end"].isoformat(),
                       "exit_time": timestamp.isoformat(), "cross_id": position["cross_id"],
                       "quantity_btc": position["quantity"], "entry_price_jpy": position["entry_price"],
                       "exit_price_jpy": fill, "entry_atr": position["atr"],
                       "stop_price_jpy": position["stop"], "target_price_jpy": position["target"],
                       "entry_outlay_jpy": position["outlay"], "exit_net_proceeds_jpy": proceeds - sell_fee,
                       "pnl_jpy": pnl, "fees_jpy": position["buy_fee"] + sell_fee,
                       "holding_hours": holding, "exit_reason": reason, "forced_end": forced_exit})
        if forced_exit:
            forced += 1
        position = None

    for index in range(start_index, end_index):
        bar = dataset.candles[index]
        decision = by_index.get(index)
        if position is None and decision is not None and decision.valid and decision.cross_id not in used_crosses:
            fill = bar.open * (1 + impact)
            # Decimal floor guarantees the accepted notional plus fees never
            # exceeds the configured yen ceiling, even near a quantity boundary.
            step = Decimal(str(POLICY["btc_step"]))
            cash_ceiling = min(budget, max(0.0, cash))
            q = (Decimal(str(cash_ceiling)) / (Decimal(str(fill)) * (1 + Decimal(str(fee_rate)))) / step)
            quantity = float(q.to_integral_value(rounding=ROUND_DOWN) * step)
            gross = quantity * fill
            buy_fee = gross * fee_rate
            outlay = gross + buy_fee
            stop = fill - decision.atr * c.stop_atr
            target = fill + decision.atr * c.target_atr
            if (quantity < float(POLICY["min_btc"]) or gross < POLICY["min_notional_jpy"] or
                    outlay > cash or outlay > budget or stop <= 0):
                rejected += 1
            else:
                cash -= outlay
                fees += buy_fee
                execution_cost += quantity * (fill - bar.open) + buy_fee
                used_crosses.add(decision.cross_id)
                position = {"entry_time": bar.start, "signal_end": decision.signal_end,
                            "cross_id": decision.cross_id, "entry_price": fill, "quantity": quantity,
                            "atr": decision.atr, "stop": stop, "target": target,
                            "outlay": outlay, "buy_fee": buy_fee}
        if position is not None:
            held_bars += 1
            stop_hit, target_hit = bar.low <= position["stop"], bar.high >= position["target"]
            if stop_hit and target_hit:
                ambiguous += 1
            if bar.open <= position["stop"]:
                close_position(bar.open, bar.start, "gap_stop")
            elif stop_hit:
                close_position(position["stop"], bar.start + timedelta(minutes=5), "stop")
            elif target_hit:
                close_position(position["target"], bar.start + timedelta(minutes=5), "target")
        equity = cash + (position["quantity"] * bar.close if position is not None else 0.0)
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak * 100)
    last = dataset.candles[end_index - 1]
    end_time = last.start + timedelta(minutes=5)
    mtm = cash + (position["quantity"] * last.close if position is not None else 0.0)
    pre_liquidation_unrealized = (position["quantity"] * last.close - position["outlay"] if position is not None else 0.0)
    open_btc = position["quantity"] if position is not None else 0.0
    if position is not None:
        close_position(last.close, end_time, "forced_end", forced_exit=True)
    peak = max(peak, cash)
    drawdown = max(drawdown, (peak - cash) / peak * 100)
    natural = [t for t in trades if not t["forced_end"]]
    profits = [t["pnl_jpy"] for t in natural if t["pnl_jpy"] > 0]
    losses = [-t["pnl_jpy"] for t in natural if t["pnl_jpy"] < 0]
    # The concentration guard includes positive end valuation so that one large
    # unclosed gain cannot hide reliance on a single outcome.
    all_profits = [t["pnl_jpy"] for t in trades if t["pnl_jpy"] > 0]
    gross_profit, gross_loss = sum(profits), sum(losses)
    closed = len(natural)
    realized = sum(t["pnl_jpy"] for t in natural)
    period_end_pnl = sum(t["pnl_jpy"] for t in trades if t["forced_end"])
    metrics = Metrics(initial, cash - initial, (cash / initial - 1) * 100,
                      cash, mtm, cash, realized, pre_liquidation_unrealized,
                      drawdown, gross_profit / gross_loss if gross_loss else None,
                      closed, len(profits), len(losses), len(profits) / closed * 100 if closed else 0.0,
                      gross_profit, gross_loss, max(all_profits) / sum(all_profits) if all_profits else 0.0,
                      held_bars / (end_index - start_index) * 100, max_hold,
                      fees, execution_cost, open_btc, ambiguous, rejected, forced,
                      len(trades), period_end_pnl, rejected_exits)
    return Evaluation(c.strategy_hash, dataset.sha256, start_index, end_index,
                      dataset.candles[start_index].start.isoformat(), end_time.isoformat(), mult,
                      metrics, tuple(trades),
                      {"simulation_only": True, "source": dataset.source, "market_evidence": False,
                       "fee_bps_per_side": ex["fee_bps_per_side"] * mult,
                       "spread_bps_round_trip": ex["spread_bps_round_trip"] * mult,
                       "slippage_bps_per_side": ex["slippage_bps_per_side"] * mult,
                       "buy_budget_jpy": budget, "fill": "next_5m_open", "entry_atr_frozen": True,
                       "same_bar_both_hit": "stop_first", "gap_stop": "worse_open_with_costs",
                       "end_liquidation": "valuation_only_with_costs; excludes_order_minimum; not_a_natural_exit",
                       "closed_trades_and_profit_factor": "natural_exits_only; excludes_forced_end",
                       "profit_concentration": "includes_positive_period_end_valuation",
                       "indicator_history_bars": ex["indicator_history_bars"],
                       "open_position_btc_and_unrealized_pnl": "before_synthetic_end_liquidation",
                       "exposure": "five_minute_bars_with_any_position; intrabar_duration_unknown",
                       "max_drawdown": "five_minute_close_equity_and_final_liquidation; excludes_intrabar_extremes",
                       "execution_limits": "OHLC simulation; no order_book, partial_fills, latency_or_liquidity_model"})


def evaluate(dataset: Dataset, candidate: Candidate = BASELINE, *,
             start_index: int | None = None, end_index: int | None = None,
             cost_multiplier: float = 1.0, buy_budget_jpy: float = 50_000.0) -> Evaluation:
    """Evaluate one time segment, flat at both boundaries, with prefix warmup."""
    end = len(dataset.candles) if end_index is None else end_index
    start = chronological_split(dataset)["train"][0] if start_index is None else start_index
    return simulate(dataset, candidate, build_signals(dataset, candidate, end_index=end),
                    start_index=start, end_index=end, cost_multiplier=cost_multiplier,
                    buy_budget_jpy=buy_budget_jpy)
