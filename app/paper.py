"""Deterministic simulated orderbook fills. No authenticated exchange APIs."""
from __future__ import annotations
from decimal import Decimal, ROUND_DOWN
from dataclasses import dataclass
from .config import MAX_BUY_JPY, STOP_ATR, TARGET_ATR

STEP = Decimal("0.00000001")
MIN_BTC = Decimal("0.001")
MIN_JPY = Decimal("500")
MAX_BUY = MAX_BUY_JPY  # Absolute ceiling; actual Settings.buy_jpy can be lower.

@dataclass(frozen=True)
class Fill:
    qty: Decimal
    price: Decimal
    jpy: Decimal    # gross price * quantity
    fee: Decimal = Decimal("0")
    @property
    def total_buy_jpy(self) -> Decimal:
        return self.jpy + self.fee
    @property
    def net_sell_jpy(self) -> Decimal:
        return self.jpy - self.fee

def _fee_rate(bps: Decimal) -> Decimal:
    bps=Decimal(str(bps))
    if not bps.is_finite() or not 0 <= bps <= 100:
        raise ValueError("INVALID_PAPER_FEE")
    return bps/Decimal("10000")

def buy_from_asks(asks: list[tuple[Decimal, Decimal]], budget: Decimal,
                  fee_bps: Decimal = Decimal("0")) -> Fill | None:
    """The inclusive cash outlay (not just gross notional) <= budget."""
    budget=Decimal(str(budget))
    if not budget.is_finite() or not Decimal("0") < budget <= MAX_BUY:
        raise ValueError("BUY_BUDGET_CAP")
    rate=_fee_rate(fee_bps)
    remaining=budget
    qty=Decimal("0")
    gross=Decimal("0")
    for price,depth in sorted(asks,key=lambda x:x[0]):
        price,depth=Decimal(str(price)),Decimal(str(depth))
        if not (price.is_finite() and depth.is_finite()) or price<=0 or depth<=0:
            continue
        chunk=min(depth,remaining/(price*(1+rate))).quantize(STEP,rounding=ROUND_DOWN)
        if chunk<=0:continue
        cost=chunk*price*(1+rate)
        qty+=chunk
        gross+=chunk*price
        remaining-=cost
        if remaining < price*STEP*(1+rate):break
    if qty<MIN_BTC or gross<MIN_JPY:return None
    if remaining>=Decimal("0.00000001") and asks:
        # If a partial book can't spend the full budget, avoid inventing depth.
        valid=[Decimal(str(p)) for p,dep in asks
               if Decimal(str(p)).is_finite() and Decimal(str(dep)).is_finite()
               and Decimal(str(p))>0 and Decimal(str(dep))>0]
        if valid and remaining >= max(valid)*STEP*(1+rate):
            return None
    fee=gross*rate
    if gross+fee>budget:return None
    return Fill(qty,gross/qty,gross,fee)

def sell_to_bids(bids:list[tuple[Decimal,Decimal]], quantity:Decimal,
                 fee_bps:Decimal=Decimal("0"))->Fill|None:
    quantity=Decimal(str(quantity)); rate=_fee_rate(fee_bps)
    if not quantity.is_finite() or quantity<MIN_BTC or quantity%STEP:return None
    left=quantity
    gross=Decimal("0")
    for price,depth in sorted(bids,key=lambda x:x[0],reverse=True):
        price,depth=Decimal(str(price)),Decimal(str(depth))
        if not (price.is_finite() and depth.is_finite()) or price<=0 or depth<=0:
            continue
        part=min(left,depth.quantize(STEP,rounding=ROUND_DOWN))
        if part<=0:continue
        gross+=part*price
        left-=part
        if left==0:break
    if left or gross<MIN_JPY:return None
    return Fill(quantity,gross/quantity,gross,gross*rate)

def bracket(entry_price:Decimal, atr:Decimal)->tuple[Decimal,Decimal]:
    """Immutable SL/TP at fill: ATR14 x3 / x12 (do not modify multipliers)."""
    if not Decimal(str(entry_price)).is_finite() or not Decimal(str(atr)).is_finite():
        raise ValueError("Invalid price/ATR")
    if entry_price<=0 or atr<=0:raise ValueError("invalid ATR/entry")
    stop=entry_price-atr*STOP_ATR
    target=entry_price+atr*TARGET_ATR
    if stop<=0:raise ValueError("stop below zero")
    return stop,target
