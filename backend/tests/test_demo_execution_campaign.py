from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.demo_execution import (
    DEMO_BROKER,
    DEMO_MODE,
    DEMO_SERVER,
    REAL_MODE,
    DemoExecutionEngine,
    ExecutionConfig,
    ExecutionJournal,
    Signal,
    account_guard,
    append_outcome,
    demo_metrics,
    learning_rows,
)


class MT5:
    TRADE_ACTION_DEAL = 1; ORDER_TYPE_BUY = 2; ORDER_TYPE_SELL = 3; ORDER_TIME_GTC = 4; ORDER_FILLING_IOC = 5
    TRADE_RETCODE_DONE = 10009; TRADE_RETCODE_DONE_PARTIAL = 10010
    def __init__(self, account=None, check=10009, send=10009):
        self.account = account or SimpleNamespace(trade_mode=DEMO_MODE, company=DEMO_BROKER, server=DEMO_SERVER, trade_allowed=True, login=1)
        self.check, self.send, self.send_calls = check, send, 0
    def account_info(self): return self.account
    def symbol_info(self, _): return SimpleNamespace(point=.00001, trade_tick_value=.01, trade_tick_size=.00001, volume_min=.01, volume_step=.01, volume_max=100)
    def symbol_info_tick(self, _): return SimpleNamespace(ask=1.10010, bid=1.10000)
    def order_check(self, _): return SimpleNamespace(retcode=self.check, comment="check")
    def order_send(self, _):
        self.send_calls += 1; return SimpleNamespace(retcode=self.send, comment="ok", order=7, deal=8, price=1.10010, volume=.01)


def signal(decision="BUY", **changes):
    base = dict(prediction_id="p1", v1_snapshot_id="v1-paper-r4-0d891a2356e35464", v1_config_hash="0d891a2356e35464dbe3ba9f51c50fe240e40feff84fe4008d8b1564b55f9eac", symbol="EURUSD", timeframe="1m", candle_timestamp="2026-08-18T00:00:00+00:00", decision=decision, confidence=.7, entry=Decimal("1.10010"), stop=Decimal("1.09910"), target=Decimal("1.10210"))
    return Signal(**(base | changes))


def enabled(**changes):
    return ExecutionConfig(execution_mode="DEMO_EXECUTION", trading_execution_enabled=True, demo_execution_enabled=True, real_execution_enabled=False, kill_switch=False, **changes)


@pytest.mark.parametrize("config", [ExecutionConfig(), ExecutionConfig(execution_mode="DEMO_EXECUTION", kill_switch=False), ExecutionConfig(execution_mode="DEMO_EXECUTION", trading_execution_enabled=True, demo_execution_enabled=True, real_execution_enabled=True, kill_switch=False)])
def test_non_authorized_modes_never_send(tmp_path, config):
    mt5 = MT5(); result = DemoExecutionEngine(mt5, ExecutionJournal(tmp_path / "x.jsonl"), config).execute(signal(), worker_owns_lease=True)
    assert result["status"] == "BLOCKED" and mt5.send_calls == 0


def test_real_and_wrong_server_hard_blocked():
    with pytest.raises(Exception, match="REAL_ACCOUNT_EXECUTION_BLOCKED"):
        account_guard(SimpleNamespace(trade_mode=REAL_MODE, company=DEMO_BROKER, server=DEMO_SERVER))
    with pytest.raises(Exception, match="DEMO_SERVER_MISMATCH"):
        account_guard(SimpleNamespace(trade_mode=DEMO_MODE, company=DEMO_BROKER, server="wrong"))


@pytest.mark.parametrize("bad", [signal("NO_TRADE"), signal(v1_snapshot_id="bad"), signal(v1_config_hash="bad"), signal(stop=Decimal("1.10100"))])
def test_invalid_or_no_trade_never_sends(tmp_path, bad):
    mt5 = MT5(); assert DemoExecutionEngine(mt5, ExecutionJournal(tmp_path / "x"), enabled()).execute(bad, worker_owns_lease=True)["status"] == "BLOCKED"; assert mt5.send_calls == 0


def test_check_failure_duplicate_and_lease_prevent_send(tmp_path):
    path = tmp_path / "x"; mt5 = MT5(check=1); engine = DemoExecutionEngine(mt5, ExecutionJournal(path), enabled())
    assert engine.execute(signal(), worker_owns_lease=True)["status"] == "ORDER_CHECK_FAILED" and mt5.send_calls == 0
    assert engine.execute(signal(), worker_owns_lease=True)["reason"] == "EXECUTION_ALREADY_EXISTS"
    assert engine.execute(signal(prediction_id="p2"), worker_owns_lease=False)["status"] == "BLOCKED"


def test_fill_is_journaled_and_outcome_builds_read_only_learning_data(tmp_path):
    journal = ExecutionJournal(tmp_path / "x"); result = DemoExecutionEngine(MT5(), journal, enabled()).execute(signal(), worker_owns_lease=True)
    assert result["status"] == "FILLED" and result["account_mode"] == "DEMO" and journal.contains(signal())
    append_outcome(journal, result, exit_price=Decimal("1.10210"), gross_pnl=Decimal("10"), exit_reason="TP")
    assert learning_rows(journal.records())[0]["outcome"] == "WIN"
    assert demo_metrics(journal.records())["filled_orders"] == 1


def test_network_ambiguity_and_safety_limits_prevent_retry_or_new_order(tmp_path, monkeypatch):
    frozen_now = datetime.now(UTC)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now.replace(tzinfo=None) if tz is None else frozen_now.astimezone(tz)

    monkeypatch.setattr("app.demo_execution.datetime", FrozenDateTime)
    journal = ExecutionJournal(tmp_path / "x")
    mt5 = MT5(send=1); engine = DemoExecutionEngine(mt5, journal, enabled())
    assert engine.execute(signal(), worker_owns_lease=True)["status"] == "REJECTED"
    assert engine.execute(signal(), worker_owns_lease=True)["reason"] == "EXECUTION_ALREADY_EXISTS"
    # A prior loss beyond the daily limit blocks an otherwise valid fresh signal.
    journal.append({"record_type": "outcome", "closed_at": frozen_now.isoformat(), "r_multiple": "-3", "result": "LOSS"})
    assert engine.execute(signal(prediction_id="p2"), worker_owns_lease=True)["reason"] == "DAILY_LOSS_LIMIT_REACHED"


def test_account_change_and_owned_position_guard(tmp_path):
    mt5 = MT5(); journal = ExecutionJournal(tmp_path / "x"); engine = DemoExecutionEngine(mt5, journal, enabled())
    engine.execute(signal(), worker_owns_lease=True)
    mt5.account.login = 2
    assert engine.execute(signal(prediction_id="p2"), worker_owns_lease=True)["reason"] == "ACCOUNT_CHANGED_DURING_RUNTIME"
    mt5 = MT5(); mt5.positions_get = lambda: [SimpleNamespace(magic=26081801)]
    assert DemoExecutionEngine(mt5, ExecutionJournal(tmp_path / "y"), enabled()).execute(signal(), worker_owns_lease=True)["reason"] == "MAX_NAVORA_DEMO_POSITIONS_REACHED"
