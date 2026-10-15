from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.demo_execution_v1 import (
    DEMO_MODE,
    POLICY_HASH,
    DemoExecutionAdapter,
    ExecutionBlocked,
    Journal,
    account_guard,
    calculation_only,
    execution_enabled,
    guard,
)


class MT5:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1

    def __init__(self, mode=DEMO_MODE, server="ICMarketsSC-Demo", currency="USD"):
        self.a = SimpleNamespace(
            trade_mode=mode,
            server=server,
            currency=currency,
            equity=Decimal("5000200"),
            balance=Decimal("5000200"),
            margin_free=Decimal("5000000"),
        )
        self.sent = 0

    def account_info(self):
        return self.a

    def symbol_info_tick(self, _):
        return SimpleNamespace(ask=1.1, bid=1.0999)

    def symbol_info(self, _):
        return SimpleNamespace(
            trade_tick_size=0.00001,
            trade_tick_value=1,
            volume_min=0.01,
            volume_step=0.01,
            volume_max=100,
        )

    def positions_get(self):
        return []

    def order_send(self, _):
        self.sent += 1
        return SimpleNamespace(retcode=10009, order=42, price=1.1)


def test_demo_gate_and_dynamic_point_one_percent(tmp_path):
    mt5 = MT5()
    outcome = calculation_only("p", "BUY", mt5, Journal(tmp_path / "journal"), Decimal(".001"))
    assert outcome.allowed_risk_usd == Decimal("5000.200")
    assert outcome.actual_risk_usd <= outcome.allowed_risk_usd
    assert outcome.target - outcome.entry == (outcome.entry - outcome.stop) * 2
    with pytest.raises(ExecutionBlocked):
        account_guard(MT5(mode=2).account_info())
    with pytest.raises(ExecutionBlocked):
        account_guard(MT5(server="wrong").account_info())
    with pytest.raises(ExecutionBlocked):
        account_guard(MT5(currency="EUR").account_info())


def test_dedup_default_disable_and_no_order_send(tmp_path, monkeypatch):
    mt5, journal = MT5(), Journal(tmp_path / "journal")
    journal.append({"record_type": "ORDER_INTENT", "parent_prediction_id": "p"})
    with pytest.raises(ExecutionBlocked, match="ALREADY"):
        calculation_only("p", "SELL", mt5, journal, Decimal(".001"))
    monkeypatch.setenv("DEMO_EXECUTION_ENABLED", "false")
    assert not execution_enabled()
    fresh = calculation_only("next", "BUY", mt5, journal, Decimal(".001"))
    with pytest.raises(ExecutionBlocked, match="DISABLED"):
        DemoExecutionAdapter(mt5, journal, tmp_path / "state").submit(fresh)
    assert mt5.sent == 0


def test_daily_loss_consecutive_loss_and_drawdown_guards(tmp_path):
    mt5, today = MT5(), datetime.now(UTC).isoformat()
    journal = Journal(tmp_path / "daily")
    journal.append(
        {"record_type": "POSITION_CLOSED", "close_timestamp": today, "net_pnl_usd": "-50010"}
    )
    with pytest.raises(ExecutionBlocked, match="DAILY"):
        guard(mt5.account_info(), journal, mt5)
    journal = Journal(tmp_path / "losses")
    for _ in range(5):
        journal.append(
            {"record_type": "POSITION_CLOSED", "close_timestamp": today, "net_pnl_usd": "-1"}
        )
    with pytest.raises(ExecutionBlocked, match="CONSECUTIVE"):
        guard(mt5.account_info(), journal, mt5)
    journal = Journal(tmp_path / "drawdown")
    journal.append({"record_type": "ORDER_INTENT", "balance_before": "5000200"})
    mt5.a.equity = Decimal("4700000")
    with pytest.raises(ExecutionBlocked, match="DRAWDOWN"):
        guard(mt5.account_info(), journal, mt5)


def test_journal_hash_and_state_are_non_sensitive(tmp_path):
    mt5, journal = MT5(), Journal(tmp_path / "journal")
    record = journal.append({"record_type": "ORDER_INTENT", "parent_prediction_id": "p"})
    assert record["execution_policy_hash"] == POLICY_HASH
    assert record["record_hash"]
    adapter = DemoExecutionAdapter(mt5, journal, tmp_path / "state.json")
    adapter.write_state("READY")
    assert "login" not in (tmp_path / "state.json").read_text(encoding="utf8")


def test_min_lot_and_max_position_fail_closed(tmp_path):
    mt5 = MT5()
    mt5.a.equity = Decimal("1")
    mt5.a.balance = Decimal("1")
    with pytest.raises(ExecutionBlocked, match="MIN_LOT"):
        calculation_only("tiny", "BUY", mt5, Journal(tmp_path / "journal"), Decimal(".001"))

    mt5 = MT5()
    mt5.positions_get = lambda: [object()]
    with pytest.raises(ExecutionBlocked, match="POSITIONS"):
        calculation_only("open", "BUY", mt5, Journal(tmp_path / "positions"), Decimal(".001"))
