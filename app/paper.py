"""Deterministic market-depth paper fills, no order dispatch."""
from __future__ import annotations
from decimal import Decimal, ROUND_DOWN
from dataclasses import dataclass

STEP=Decimal('0.00000001')
MIN_BTC=Decimal('0.001')
MIN_JPY=Decimal('500')
MAX_BUY=Decimal('50000')

@dataclass(frozen=True)
class Fill:
    qty:Decimal
    price:Decimal
    jpy:Decimal


def buy_from_asks(asks:list[tuple[Decimal,Decimal]], budget:Decimal)->Fill|None:
    if budget<=0 or budget>MAX_BUY:raise ValueError('BUY_BUDGET_CAP')
    available=Decimal(budget);qty=Decimal(0);paid=Decimal(0)
    for price,depth in sorted(asks,key=lambda x:x[0]):
        if price<=0 or depth<=0:continue
        chunk=min(depth,available/price).quantize(STEP,rounding=ROUND_DOWN)
        if chunk<=0:continue
        qty+=chunk;spent=chunk*price;paid+=spent;available-=spent
        if available < price*STEP:break
    if qty<MIN_BTC or paid<MIN_JPY or paid>budget:return None
    if available>max(p for p,d in asks)*STEP:return None
    return Fill(qty,paid/qty,paid)


def sell_to_bids(bids:list[tuple[Decimal,Decimal]],quantity:Decimal)->Fill|None:
    if quantity<MIN_BTC or quantity%STEP:return None
    left=quantity;received=Decimal(0)
    for price,depth in sorted(bids,key=lambda x:x[0],reverse=True):
        if price<=0 or depth<=0:continue
        part=min(left,depth.quantize(STEP,rounding=ROUND_DOWN))
        if part<=0:continue
        received+=part*price;left-=part
        if left==0:break
    if left or received<MIN_JPY:return None
    return Fill(quantity,received/quantity,received)


def bracket(entry_price:Decimal, atr:Decimal)->tuple[Decimal,Decimal]:
    if entry_price<=0 or atr<=0:raise ValueError('invalid ATR/entry')
    stop=entry_price-atr*Decimal(3)
    target=entry_price+atr*Decimal(12)
    if stop<=0:raise ValueError('stop below zero')
    return stop,target
