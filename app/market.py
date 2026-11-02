"""Only public Coincheck websocket and public order-book through CCXT."""
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from math import isfinite


@dataclass(frozen=True)
class TradeTick:
    trade_id: str
    at: datetime
    price: Decimal
    quantity: Decimal


def parse_coincheck_trades(message: str) -> list[TradeTick]:
    obj=json.loads(message)
    if not isinstance(obj,list) or not obj or not isinstance(obj[0],list):return []
    result=[]
    for row in obj:
        if not isinstance(row,list) or len(row)<6 or row[2]!='btc_jpy':continue
        try:
            stamp=int(row[0]);id_=str(int(row[1]))
            price=Decimal(str(row[3]));qty=Decimal(str(row[4]))
            if not 1_200_000_000<stamp<4_102_444_800 or price<=0 or qty<=0:continue
            result.append(TradeTick(id_,datetime.fromtimestamp(stamp,timezone.utc),price,qty))
        except (ValueError,TypeError,ArithmeticError): continue
    return result


class PublicQuote:
    def __init__(self):
        import ccxt
        self.exchange=ccxt.coincheck({'enableRateLimit':True,'timeout':10000})

    def fetch(self):
        # CCXT public endpoint only. No keys or create_order anywhere in repository.
        orderbook=self.exchange.fetch_order_book('BTC/JPY')
        def side(name):
            return [(Decimal(str(row[0])),Decimal(str(row[1])))
                    for row in orderbook.get(name,[]) if len(row)>=2 and Decimal(str(row[1]))>0]
        bids=side('bids');asks=side('asks')
        if not asks or not bids:raise ValueError('Missing public orderbook liquidity')
        return asks,bids
