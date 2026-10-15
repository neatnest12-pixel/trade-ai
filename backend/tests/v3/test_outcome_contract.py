from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.v3.contracts import Outcome
from app.v3.outcomes import (
    ATR_PERIOD,
    MAX_HOLD,
    RR_MATRIX,
    STOP_MULTIPLIER,
    V3_OUTCOME_POLICY_HASH,
    V3_OUTCOME_POLICY_VERSION,
    OutcomeCandle,
    OutcomeTick,
    atr_stop_contract,
    candle_ambiguity,
    first_executable_entry,
    resolve_trade,
    stop_and_target,
)

BASE = datetime(2026, 1, 1, 12, tzinfo=UTC)


def tick(seconds: int, bid: float, ask: float) -> OutcomeTick:
    return OutcomeTick(BASE + timedelta(seconds=seconds), bid, ask)


def m15_candles(count: int = ATR_PERIOD + 1, start: float = 1.10) -> list[OutcomeCandle]:
    out = []
    for i in range(count):
        ts = BASE - timedelta(minutes=15 * (count - i))
        close = start + i * 0.0001
        out.append(OutcomeCandle(ts, close + 0.0010, close - 0.0010, close))
    return out


def test_policy_identity_and_rr_matrix() -> None:
    assert V3_OUTCOME_POLICY_VERSION == "v3-outcome-r1"
    assert len(V3_OUTCOME_POLICY_HASH) == 64
    assert RR_MATRIX == (1, 1.5, 2, 3, 5)
    assert MAX_HOLD == timedelta(hours=6)
    assert STOP_MULTIPLIER == 1.5


def test_entry_uses_first_valid_tick_strictly_after_decision() -> None:
    ticks = [tick(0, 1.1000, 1.1002), tick(1, 1.1001, 1.1003)]

    buy = first_executable_entry(BASE, "BUY", ticks)
    sell = first_executable_entry(BASE, "SELL", ticks)

    assert buy is not None and buy.price == 1.1003
    assert sell is not None and sell.price == 1.1001
    assert buy.timestamp > BASE


def test_atr_uses_completed_m15_only_at_decision_time() -> None:
    candles = m15_candles()
    future = OutcomeCandle(BASE, 9.0, 0.5, 8.0)

    contract = atr_stop_contract(BASE, [*candles, future])

    assert contract is not None
    assert contract.atr_timestamp < BASE
    assert contract.atr_value < 1
    assert contract.stop_distance == contract.atr_value * STOP_MULTIPLIER


def test_buy_uses_ask_entry_bid_exit_and_tp_ordering() -> None:
    candles = m15_candles()
    atr = atr_stop_contract(BASE, candles)
    assert atr is not None
    entry_tick = tick(1, 1.1000, 1.1002)
    _, target = stop_and_target(entry_tick.ask, "BUY", atr.stop_distance, 1)
    result = resolve_trade(
        decision_timestamp=BASE,
        side="BUY",
        rr=1,
        ticks=[entry_tick, OutcomeTick(BASE + timedelta(seconds=2), target, target + 0.0002)],
        m15_candles=candles,
    )

    assert result.outcome == Outcome.TP_HIT
    assert result.entry == entry_tick.ask
    assert result.exit_price == target
    assert result.gross_r == 1


def test_sell_uses_bid_entry_ask_exit_and_sl_ordering() -> None:
    candles = m15_candles()
    atr = atr_stop_contract(BASE, candles)
    assert atr is not None
    entry_tick = tick(1, 1.1000, 1.1002)
    stop, _ = stop_and_target(entry_tick.bid, "SELL", atr.stop_distance, 1.5)
    result = resolve_trade(
        decision_timestamp=BASE,
        side="SELL",
        rr=1.5,
        ticks=[entry_tick, OutcomeTick(BASE + timedelta(seconds=2), stop - 0.0002, stop)],
        m15_candles=candles,
    )

    assert result.outcome == Outcome.SL_HIT
    assert result.entry == entry_tick.bid
    assert result.exit_price == stop
    assert result.gross_r == -1


def test_time_exit_uses_actual_realized_r() -> None:
    candles = m15_candles()
    entry_tick = tick(1, 1.1000, 1.1002)
    exit_tick = OutcomeTick(BASE + timedelta(seconds=1) + MAX_HOLD, 1.1005, 1.1007)
    result = resolve_trade(
        decision_timestamp=BASE,
        side="BUY",
        rr=2,
        ticks=[entry_tick, exit_tick],
        m15_candles=candles,
    )

    assert result.outcome == Outcome.TIME_EXIT
    assert result.gross_r is not None
    assert result.gross_r != 0


def test_ambiguity_and_unavailable_paths() -> None:
    assert candle_ambiguity("BUY", high=1.2, low=1.0, stop=1.05, target=1.15) == Outcome.AMBIGUOUS
    assert resolve_trade(
        decision_timestamp=BASE,
        side="BUY",
        rr=1,
        ticks=[],
        m15_candles=m15_candles(),
    ).outcome == Outcome.ENTRY_UNAVAILABLE
    assert resolve_trade(
        decision_timestamp=BASE,
        side="BUY",
        rr=1,
        ticks=[tick(1, 1.1000, 1.1002)],
        m15_candles=[],
    ).outcome == Outcome.ATR_UNAVAILABLE
