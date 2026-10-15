from decimal import Decimal
from pathlib import Path

import pytest

from app.v1_1.contract import (
    CONFIG_HASH,
    MAX_RISK_RUPEES,
    SNAPSHOT_ID,
    STARTING_BALANCE,
    close_trade,
    evaluate_candle,
    freeze_trade,
)
from app.v1_1.journal import append_v11
from app.v1_1.metrics import metrics


def plan(side="BUY", balance=STARTING_BALANCE):
    return freeze_trade(prediction_id="p", symbol="EURUSD", timeframe="1m", prediction_timestamp="t", candle_timestamp="c", decision=side, entry_price=Decimal("1.1"), atr_14_price=Decimal(".001"), balance_before_trade=balance, confidence=Decimal(".6"))

def test_identity_and_buy_sell_levels():
    assert SNAPSHOT_ID.startswith("v1.1-paper-") and len(CONFIG_HASH) == 64
    assert (plan().stop_loss, plan().target_price) == (Decimal("1.099"), Decimal("1.102"))
    assert (plan("SELL").stop_loss, plan("SELL").target_price) == (Decimal("1.101"), Decimal("1.098"))

def test_no_trade_risk_and_position_size():
    assert plan("NO_TRADE").position_size_simulated is None
    assert plan( balance=Decimal("100000")).risk_rupees == MAX_RISK_RUPEES
    assert plan(balance=Decimal("1000")).risk_rupees == Decimal("10")
    assert plan().position_size_simulated == Decimal("0.00297619")

def test_outcomes_and_economics():
    p = plan()
    assert evaluate_candle(p, high=Decimal("1.102"), low=Decimal("1.1"), close=Decimal("1.1"), exit_timestamp="t", elapsed_candles=1, peak_balance=STARTING_BALANCE).status == "TAKE_PROFIT"
    assert evaluate_candle(p, high=Decimal("1.1"), low=Decimal("1.099"), close=Decimal("1.1"), exit_timestamp="t", elapsed_candles=1, peak_balance=STARTING_BALANCE).status == "STOP_LOSS"
    assert evaluate_candle(p, high=Decimal("1.102"), low=Decimal("1.099"), close=Decimal("1.1"), exit_timestamp="t", elapsed_candles=1, peak_balance=STARTING_BALANCE).status == "AMBIGUOUS_INTRABAR"
    assert evaluate_candle(p, high=Decimal("1.101"), low=Decimal("1.1"), close=Decimal("1.101"), exit_timestamp="t", elapsed_candles=5, peak_balance=STARTING_BALANCE).status == "TIME_EXIT"
    win = close_trade(p, "TAKE_PROFIT", Decimal("1.102"), "t", STARTING_BALANCE)
    next_trade = plan(balance=win.balance_after_trade or STARTING_BALANCE)
    loss = close_trade(next_trade, "STOP_LOSS", Decimal("1.099"), "t", win.peak_balance or STARTING_BALANCE)
    assert (win.realized_r, win.realized_pnl_rupees, loss.drawdown_rupees) == (Decimal("2"), Decimal("50"), Decimal("25"))
    assert metrics([win, loss])["profit_factor"] == Decimal("2")

def test_journal_duplicate_and_v1_rejection(tmp_path: Path):
    path = tmp_path / "FORWARD_PAPER_JOURNAL_V1_1.jsonl"
    append_v11(path, {"prediction_hash": "x", "campaign_id": "NAVORA-QUANT-V1.1-PAPER", "snapshot_id": SNAPSHOT_ID, "config_hash": CONFIG_HASH, "symbol": "EURUSD", "timeframe": "M1", "prediction_timestamp": "t", "candle_timestamp": "c", "decision": "NO_TRADE", "entry_price": None, "stop_loss": None, "target_price": None, "risk_rupees": None, "position_size_simulated": None})
    with pytest.raises(ValueError): append_v11(path, {"prediction_hash": "x", "campaign_id": "NAVORA-QUANT-V1.1-PAPER", "snapshot_id": SNAPSHOT_ID, "config_hash": CONFIG_HASH, "symbol": "EURUSD", "timeframe": "M1", "prediction_timestamp": "t", "candle_timestamp": "c", "decision": "NO_TRADE", "entry_price": None, "stop_loss": None, "target_price": None, "risk_rupees": None, "position_size_simulated": None})
    with pytest.raises(ValueError): append_v11(tmp_path / "FORWARD_PAPER_JOURNAL.jsonl", {"prediction_hash": "x"})





def test_campaign_schema_and_identity_fail_closed(tmp_path: Path):
    path = tmp_path / "new.jsonl"
    base = {"prediction_hash": "x", "campaign_id": "NAVORA-QUANT-V1.1-PAPER",
            "snapshot_id": SNAPSHOT_ID, "config_hash": CONFIG_HASH, "symbol": "EURUSD",
            "timeframe": "M1", "prediction_timestamp": "t", "candle_timestamp": "c",
            "decision": "NO_TRADE", "entry_price": None, "stop_loss": None,
            "target_price": None, "risk_rupees": None, "position_size_simulated": None}
    append_v11(path, base)
    for field in ("campaign_id", "snapshot_id", "config_hash"):
        bad = dict(base, prediction_hash=field + "bad", **{field: "wrong"})
        with pytest.raises(ValueError):
            append_v11(tmp_path / (field + ".jsonl"), bad)
    with pytest.raises(ValueError):
        append_v11(tmp_path / "missing.jsonl", {k: v for k, v in base.items() if k != "campaign_id"})

