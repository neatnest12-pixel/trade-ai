from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from .contracts import Outcome

V3_OUTCOME_POLICY_VERSION = "v3-outcome-r1"
V3_OUTCOME_POLICY_HASH = hashlib.sha256(V3_OUTCOME_POLICY_VERSION.encode()).hexdigest()
RR_MATRIX = (1, 1.5, 2, 3, 5)
ATR_TIMEFRAME = "M15"
ATR_PERIOD = 14
STOP_MULTIPLIER = 1.5
MAX_HOLD = timedelta(hours=6)


@dataclass(frozen=True)
class OutcomeTick:
    timestamp: datetime
    bid: float
    ask: float
    provenance: str = "MT5_TICK"

    def valid(self) -> bool:
        return self.bid > 0 and self.ask > 0 and self.ask >= self.bid


@dataclass(frozen=True)
class OutcomeCandle:
    timestamp: datetime
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class TradeOutcome:
    outcome: Outcome
    entry: float | None
    stop: float | None
    target: float | None
    exit_price: float | None
    gross_r: float | None
    spread_cost_r: float | None
    commission_cost_r: float | None
    net_r: float | None
    policy_version: str = V3_OUTCOME_POLICY_VERSION
    policy_hash: str = V3_OUTCOME_POLICY_HASH


@dataclass(frozen=True)
class EntryContract:
    timestamp: datetime
    price: float
    bid: float
    ask: float
    spread: float
    provenance: str


@dataclass(frozen=True)
class StopContract:
    atr_timeframe: str
    atr_period: int
    atr_value: float
    atr_timestamp: datetime
    stop_multiplier: float
    stop_distance: float


def assert_rr(rr: float) -> None:
    if rr not in RR_MATRIX:
        raise ValueError("unsupported RR")


def first_executable_entry(
    decision_timestamp: datetime,
    side: str,
    ticks: list[OutcomeTick],
) -> EntryContract | None:
    for tick in sorted(ticks, key=lambda item: item.timestamp):
        if tick.timestamp > decision_timestamp and tick.valid():
            price = tick.ask if side == "BUY" else tick.bid
            return EntryContract(
                timestamp=tick.timestamp,
                price=price,
                bid=tick.bid,
                ask=tick.ask,
                spread=tick.ask - tick.bid,
                provenance=tick.provenance,
            )
    return None


def atr_stop_contract(
    decision_timestamp: datetime,
    m15_candles: list[OutcomeCandle],
) -> StopContract | None:
    completed = sorted(
        (candle for candle in m15_candles if candle.timestamp + timedelta(minutes=15) <= decision_timestamp),
        key=lambda candle: candle.timestamp,
    )
    if len(completed) < ATR_PERIOD + 1:
        return None
    true_ranges = []
    window = completed[-ATR_PERIOD - 1 :]
    for previous, current in zip(window[:-1], window[1:], strict=True):
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    atr_value = sum(true_ranges) / ATR_PERIOD
    if atr_value <= 0:
        return None
    return StopContract(
        atr_timeframe=ATR_TIMEFRAME,
        atr_period=ATR_PERIOD,
        atr_value=atr_value,
        atr_timestamp=completed[-1].timestamp,
        stop_multiplier=STOP_MULTIPLIER,
        stop_distance=atr_value * STOP_MULTIPLIER,
    )


def stop_and_target(entry: float, side: str, stop_distance: float, rr: float) -> tuple[float, float]:
    assert_rr(rr)
    if stop_distance <= 0:
        raise ValueError("stop distance must be positive")
    if side == "BUY":
        return entry - stop_distance, entry + stop_distance * rr
    if side == "SELL":
        return entry + stop_distance, entry - stop_distance * rr
    raise ValueError("side must be BUY or SELL")


def resolve_trade(
    *,
    decision_timestamp: datetime,
    side: str,
    rr: float,
    ticks: list[OutcomeTick],
    m15_candles: list[OutcomeCandle],
) -> TradeOutcome:
    assert_rr(rr)
    entry = first_executable_entry(decision_timestamp, side, ticks)
    if entry is None:
        return _unresolved(Outcome.ENTRY_UNAVAILABLE)
    stop_contract = atr_stop_contract(decision_timestamp, m15_candles)
    if stop_contract is None:
        return _unresolved(Outcome.ATR_UNAVAILABLE, entry=entry.price)
    stop, target = stop_and_target(entry.price, side, stop_contract.stop_distance, rr)
    max_hold_at = entry.timestamp + MAX_HOLD
    future_ticks = [tick for tick in sorted(ticks, key=lambda item: item.timestamp) if tick.timestamp > entry.timestamp and tick.valid()]
    for tick in future_ticks:
        exit_price = tick.bid if side == "BUY" else tick.ask
        if tick.timestamp <= max_hold_at:
            if side == "BUY" and exit_price <= stop:
                return _resolved(Outcome.SL_HIT, entry.price, stop, target, exit_price, -1.0)
            if side == "BUY" and exit_price >= target:
                return _resolved(Outcome.TP_HIT, entry.price, stop, target, exit_price, rr)
            if side == "SELL" and exit_price >= stop:
                return _resolved(Outcome.SL_HIT, entry.price, stop, target, exit_price, -1.0)
            if side == "SELL" and exit_price <= target:
                return _resolved(Outcome.TP_HIT, entry.price, stop, target, exit_price, rr)
        if tick.timestamp >= max_hold_at:
            realized_r = (
                (exit_price - entry.price) / stop_contract.stop_distance
                if side == "BUY"
                else (entry.price - exit_price) / stop_contract.stop_distance
            )
            return _resolved(Outcome.TIME_EXIT, entry.price, stop, target, exit_price, realized_r)
    return _unresolved(Outcome.EXPIRED, entry=entry.price, stop=stop, target=target)


def candle_ambiguity(side: str, high: float, low: float, stop: float, target: float) -> Outcome | None:
    if side == "BUY" and high >= target and low <= stop:
        return Outcome.AMBIGUOUS
    if side == "SELL" and low <= target and high >= stop:
        return Outcome.AMBIGUOUS
    return None


def _unresolved(
    outcome: Outcome,
    *,
    entry: float | None = None,
    stop: float | None = None,
    target: float | None = None,
) -> TradeOutcome:
    return TradeOutcome(outcome, entry, stop, target, None, None, None, None, None)


def _resolved(
    outcome: Outcome,
    entry: float,
    stop: float,
    target: float,
    exit_price: float,
    gross_r: float,
) -> TradeOutcome:
    return TradeOutcome(outcome, entry, stop, target, exit_price, gross_r, None, None, gross_r)
