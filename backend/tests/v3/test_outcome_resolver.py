from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.v3.outcome_resolver import (
    TARGET_RESOLVED_CLEAN_SETUPS,
    OutcomeTick,
    append_new_outcomes,
    clean_setups,
    outcome_key,
    resolve_setup,
)
from app.v3.outcomes import V3_OUTCOME_POLICY_HASH

BASE = datetime(2026, 1, 1, 12, tzinfo=UTC)


def observation(obs_id: str, side: str, *, timestamp: datetime = BASE) -> dict[str, object]:
    return {
        "observation_id": obs_id,
        "timestamp": timestamp.isoformat(),
        "strategy_policy_version": "v3-strategy-family-r1",
        "strategy_input_policy_version": "v3-strategy-input-r1",
        "strategy_families": {
            "TREND": {
                "setup": True,
                "side": side,
            }
        },
        "strategy_input_payload": {
            "contexts": {
                "M15": {
                    "atr14": 0.002,
                    "source_candle_timestamp": (timestamp - timedelta(minutes=15)).isoformat(),
                    "close": 1.1000,
                }
            }
        },
    }


def mixed_observation() -> dict[str, object]:
    row = observation("mixed", "BUY")
    row["strategy_families"]["BREAKOUT"] = {"setup": True, "side": "SELL"}
    return row


def no_trade_observation() -> dict[str, object]:
    row = observation("none", "BUY")
    row["strategy_families"]["TREND"] = {"setup": False, "side": "NONE"}
    return row


def tick(seconds: int, bid: float, ask: float) -> OutcomeTick:
    return OutcomeTick(BASE + timedelta(seconds=seconds), bid, ask)


def test_clean_selection_excludes_mixed_and_no_trade() -> None:
    setups = clean_setups([observation("buy", "BUY"), observation("sell", "SELL"), mixed_observation(), no_trade_observation()])

    assert [setup.direction for setup in setups] == ["BUY", "SELL"]


def test_buy_tp_buy_sl_sell_tp_sell_sl_and_spread() -> None:
    buy = clean_setups([observation("buy", "BUY")])[0]
    sell = clean_setups([observation("sell", "SELL")])[0]

    buy_tp = resolve_setup(buy, 1, [tick(1, 1.1000, 1.1002), tick(2, 1.1032, 1.1034)])
    buy_sl = resolve_setup(buy, 1, [tick(1, 1.1000, 1.1002), tick(2, 1.0972, 1.0974)])
    sell_tp = resolve_setup(sell, 1, [tick(1, 1.1000, 1.1002), tick(2, 1.0968, 1.0970)])
    sell_sl = resolve_setup(sell, 1, [tick(1, 1.1000, 1.1002), tick(2, 1.1030, 1.1032)])

    assert buy_tp["exit_reason"] == "TP_HIT"
    assert buy_sl["exit_reason"] == "SL_HIT"
    assert sell_tp["exit_reason"] == "TP_HIT"
    assert sell_sl["exit_reason"] == "SL_HIT"
    assert buy_tp["entry_price"] == 1.1002
    assert sell_tp["entry_price"] == 1.1000
    assert buy_tp["entry_spread"] > 0
    assert buy_tp["commission_status"] == "UNAVAILABLE"
    assert buy_tp["mfe_r"] >= 1
    assert buy_sl["mae_r"] >= 1
    assert sell_tp["mfe_r"] >= 1
    assert sell_sl["mae_r"] >= 1


def test_time_exit_and_immature_unresolved() -> None:
    setup = clean_setups([observation("buy", "BUY")])[0]

    time_exit = resolve_setup(setup, 1, [tick(1, 1.1000, 1.1002), OutcomeTick(BASE + timedelta(hours=6, seconds=2), 1.1004, 1.1006)])
    immature = resolve_setup(setup, 1, [tick(1, 1.1000, 1.1002), tick(60, 1.1001, 1.1003)])

    assert time_exit["exit_reason"] == "TIME_EXIT"
    assert time_exit["status"] == "RESOLVED"
    assert immature["exit_reason"] == "IMMATURE_OR_NO_FUTURE_TICK"
    assert immature["status"] == "UNRESOLVED"


def test_duplicate_prevention_and_restart_recovery(tmp_path: Path) -> None:
    obs = tmp_path / "observations.jsonl"
    out = tmp_path / "outcomes.jsonl"
    obs.write_text(__import__("json").dumps(observation("buy", "BUY")) + "\n", encoding="utf8")
    ticks = [tick(1, 1.1000, 1.1002), tick(2, 1.1032, 1.1034)]

    first = append_new_outcomes(obs, out, ticks, max_setups=1)
    second = append_new_outcomes(obs, out, ticks, max_setups=1)

    assert first["outcome_rows"] == 1
    assert second["outcome_rows"] == 1
    assert first["resolved_clean_setups"] == 1
    assert second["resolved_clean_setups"] == 1
    assert first["progress_to_100"] == 100 / TARGET_RESOLVED_CLEAN_SETUPS
    assert outcome_key("buy", 1) == __import__("hashlib").sha256(f"buy|{V3_OUTCOME_POLICY_HASH}|1".encode()).hexdigest()


def test_bounded_catch_up_processes_only_missing_setups(tmp_path: Path) -> None:
    obs = tmp_path / "observations.jsonl"
    out = tmp_path / "outcomes.jsonl"
    rows = [observation("buy1", "BUY"), observation("buy2", "BUY", timestamp=BASE + timedelta(minutes=1))]
    obs.write_text("\n".join(__import__("json").dumps(row) for row in rows) + "\n", encoding="utf8")
    ticks = [
        tick(1, 1.1000, 1.1002),
        tick(2, 1.1160, 1.1162),
        OutcomeTick(BASE + timedelta(minutes=1, seconds=1), 1.1000, 1.1002),
        OutcomeTick(BASE + timedelta(minutes=1, seconds=2), 1.1160, 1.1162),
    ]

    first = append_new_outcomes(obs, out, ticks, max_setups=1)
    second = append_new_outcomes(obs, out, ticks, max_setups=1)

    assert first["outcome_rows"] == 5
    assert second["outcome_rows"] == 10
    assert first["resolved_clean_setups"] == 1
    assert second["resolved_clean_setups"] == 2


def test_no_order_send_reference_in_resolver() -> None:
    source = Path("backend/app/v3/outcome_resolver.py").read_text(encoding="utf8")
    script = Path("backend/scripts/v3_outcome_resolver.py").read_text(encoding="utf8")

    assert "order_send(" not in source
    assert "order_send(" not in script
