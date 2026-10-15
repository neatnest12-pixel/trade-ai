"""Broker-derived sizing calculator only; no MT5 import or order path."""
from dataclasses import dataclass
from decimal import Decimal,ROUND_FLOOR
import math


@dataclass(frozen=True)
class BrokerMechanics:
    tick_size: float
    tick_value_loss: float
    volume_min: float
    volume_max: float
    volume_step: float
    contract_size: float | None = None
    point: float | None = None
    stops_level: int = 0
    account_currency: str = ''
    source: str = ''


def position_size(equity,risk_percent,entry,stop,broker):
    values=(equity,risk_percent,entry,stop,broker.tick_size,broker.tick_value_loss,broker.volume_min,broker.volume_max,broker.volume_step)
    if not all(math.isfinite(v) and v>0 for v in values) or risk_percent>100 or broker.volume_max<broker.volume_min or entry==stop:raise ValueError('INVALID_SIZING_INPUT')
    if broker.stops_level<0 or broker.stops_level and (broker.point is None or not math.isfinite(broker.point) or broker.point<=0):raise ValueError('INVALID_STOP_CONSTRAINT')
    cash=equity*risk_percent/100;distance=abs(entry-stop);loss_per_lot=distance/broker.tick_size*broker.tick_value_loss
    raw=cash/loss_per_lot;step=Decimal(str(broker.volume_step));volume=float((Decimal(str(min(raw,broker.volume_max)))/step).to_integral_value(rounding=ROUND_FLOOR)*step)
    reason=None
    if volume<broker.volume_min:volume=0;reason='MINIMUM_LOT_EXCEEDS_RISK_BUDGET'
    if broker.stops_level and (not broker.point or distance<broker.stops_level*broker.point):volume=0;reason='BROKER_STOP_DISTANCE_NOT_MET'
    return dict(cash_risk=cash,stop_pips=distance/.0001,raw_lot_size=raw,broker_normalized_lot_size=volume,
                estimated_loss_at_SL=volume*loss_per_lot,risk_budget_respected=volume*loss_per_lot<=cash+1e-9,
                reason=reason,account_currency=broker.account_currency,broker_source=broker.source,
                cost_basis='PRICE_MOVE_ONLY; fees, slippage, swaps and margin not included',execution_enabled=False)
