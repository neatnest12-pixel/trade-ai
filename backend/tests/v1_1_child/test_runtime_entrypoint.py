from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace


def runtime_module():
    path = Path(__file__).parents[2] / "scripts" / "v1_1_paper_trade_processor.py"
    spec = importlib.util.spec_from_file_location("v11_runtime", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeMT5:
    TIMEFRAME_M1 = 1

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.shutdown_calls = 0
        self.now = datetime.now(UTC).replace(second=0, microsecond=0)

    def initialize(self) -> bool:
        return self.available

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def account_info(self):
        return SimpleNamespace(server="ICMarketsSC-Demo", currency="INR")

    def symbol_info(self, _symbol: str):
        return SimpleNamespace(point=0.00001, trade_tick_size=0.00001, trade_tick_value=1,
                               trade_contract_size=100000, volume_min=0.01, volume_step=0.01, volume_max=100)

    def symbol_info_tick(self, _symbol: str):
        return SimpleNamespace(time=self.now.timestamp(), bid=84)

    def symbols_get(self):
        return []

    def symbol_select(self, _symbol: str, _visible: bool):
        return True

    def copy_rates_from_pos(self, _symbol: str, _timeframe: int, _start: int, count: int):
        return [{"time": (self.now - timedelta(minutes=count - i)).timestamp(), "open": 1.1,
                 "high": 1.1002, "low": 1.0998, "close": 1.1, "spread": 10} for i in range(count)]


def paths(tmp_path: Path):
    root = tmp_path
    parent = root / "parent.jsonl"; child = root / "child.jsonl"; state = root / "runtime" / "state.json"; lock = root / "runtime" / "lock"
    parent.write_text("", encoding="utf-8")
    return parent, child, state, lock


def make_runtime(tmp_path: Path):
    module = runtime_module(); parent, child, state, lock = paths(tmp_path)
    runtime = module.Runtime(mt5=FakeMT5(), processor_id="runtime-test", sleep=lambda _n: None,
                             parent_path=parent, child_path=child, state_path=state, lock_path=lock)
    return module, runtime, parent, child, state, lock


def test_idle_cycles_heartbeat_and_v1_state_separation(tmp_path: Path, monkeypatch) -> None:
    module, runtime, _parent, child, state, _lock = make_runtime(tmp_path)
    monkeypatch.setenv("MT5_READ_ONLY", "true"); monkeypatch.setenv("TRADING_EXECUTION_ENABLED", "false")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "V1_PAPER_SNAPSHOT.json").write_text(json.dumps({"v1_snapshot_id": "v1-paper-r4-0d891a2356e35464", "configuration_hash": "0d891a2356e35464dbe3ba9f51c50fe240e40feff84fe4008d8b1564b55f9eac"}))
    assert runtime.run(max_cycles=2) == 0
    assert not child.exists()
    saved = json.loads(state.read_text())
    assert saved["status"] == "STOPPED" and saved["trade_contract_hash"] == module.HASH
    assert not (tmp_path / "runtime" / "worker_state.json").exists()


def test_preflight_failure_does_not_acquire_lock(tmp_path: Path, monkeypatch) -> None:
    module, runtime, _parent, _child, state, lock = make_runtime(tmp_path)
    monkeypatch.setenv("MT5_READ_ONLY", "false"); monkeypatch.setenv("TRADING_EXECUTION_ENABLED", "false")
    assert runtime.run(once=True) == 2
    assert json.loads(state.read_text())["last_cycle_status"] == "PREFLIGHT_FAILED"
    assert not lock.exists()


def test_single_instance_exits_without_child_writes(tmp_path: Path, monkeypatch) -> None:
    module, first, parent, child, state, lock = make_runtime(tmp_path)
    monkeypatch.setenv("MT5_READ_ONLY", "true"); monkeypatch.setenv("TRADING_EXECUTION_ENABLED", "false")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "V1_PAPER_SNAPSHOT.json").write_text(json.dumps({"v1_snapshot_id": module.PARENT_SNAPSHOT, "configuration_hash": module.PARENT_CONFIG}))
    first.preflight(); first.processor.acquire()
    second = module.Runtime(mt5=FakeMT5(), processor_id="second", parent_path=parent, child_path=child, state_path=state, lock_path=lock)
    try:
        assert second.run(once=True) == 0
        assert first.processor.lock.is_owner() and not child.exists()
    finally:
        first.shutdown(release=True)


def official_parent(timestamp: datetime) -> dict[str, str]:
    return {"type": "prediction", "record_hash": "parent-hash", "decision": "BUY",
            "v1_snapshot_id": "v1-paper-r4-0d891a2356e35464",
            "v1_config_hash": "0d891a2356e35464dbe3ba9f51c50fe240e40feff84fe4008d8b1564b55f9eac",
            "candle_timestamp": timestamp.isoformat()}


def ready_runtime(tmp_path: Path, monkeypatch):
    module, runtime, parent, child, state, lock = make_runtime(tmp_path)
    monkeypatch.setenv("MT5_READ_ONLY", "true"); monkeypatch.setenv("TRADING_EXECUTION_ENABLED", "false")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "V1_PAPER_SNAPSHOT.json").write_text(json.dumps({"v1_snapshot_id": module.PARENT_SNAPSHOT, "configuration_hash": module.PARENT_CONFIG}))
    return module, runtime, parent, child, state, lock


def test_new_parent_restart_and_running_state_are_idempotent(tmp_path: Path, monkeypatch) -> None:
    _module, runtime, parent, child, state, _lock = ready_runtime(tmp_path, monkeypatch)
    parent.write_text(json.dumps(official_parent(runtime.mt5.now - timedelta(minutes=5))) + "\n", encoding="utf-8")
    runtime.preflight(); runtime.processor.acquire()
    try:
        runtime.cycle()
        first_heartbeat = json.loads(state.read_text())["last_heartbeat"]
        runtime.cycle()
        records = [json.loads(line) for line in child.read_text().splitlines()]
        assert len([row for row in records if row["record_type"] == "TRADE_CONTRACT"]) == 1
        saved = json.loads(state.read_text())
        assert saved["status"] == "RUNNING" and saved["last_heartbeat"] > first_heartbeat
    finally:
        runtime.shutdown(release=True)


def test_provider_outage_retries_without_child_write(tmp_path: Path, monkeypatch) -> None:
    _module, runtime, parent, child, state, _lock = ready_runtime(tmp_path, monkeypatch)
    parent.write_text(json.dumps(official_parent(runtime.mt5.now - timedelta(minutes=5))) + "\n", encoding="utf-8")
    runtime.mt5.copy_rates_from_pos = lambda *_args: None
    assert runtime.run(once=True) == 0
    assert not child.exists()
    saved = json.loads(state.read_text())
    assert saved["last_cycle_status"] == "STOPPED" and saved["last_error"] is None


def test_lease_loss_fails_and_does_not_write(tmp_path: Path, monkeypatch) -> None:
    _module, runtime, _parent, child, _state, _lock = ready_runtime(tmp_path, monkeypatch)
    runtime.preflight(); runtime.processor.acquire(); runtime.processor.lock.abandon_for_test()
    try:
        try:
            runtime.cycle()
        except Exception as error:
            assert str(error) == "PROCESSOR_LEASE_LOST"
        else:
            raise AssertionError("lease loss must stop processing")
        assert not child.exists()
    finally:
        runtime.shutdown(release=True)


class FxMT5(FakeMT5):
    def __init__(self, pair: str, base: str, quote: str, bid: str, ask: str) -> None:
        super().__init__(); self.pair, self.base, self.quote, self.bid, self.ask = pair, base, quote, bid, ask

    def account_info(self):
        return SimpleNamespace(server="ICMarketsSC-Demo", currency="USD")

    def symbols_get(self):
        return [SimpleNamespace(name=self.pair, currency_base=self.base, currency_profit=self.quote)]

    def symbol_info_tick(self, symbol: str):
        if symbol == self.pair:
            return SimpleNamespace(time=self.now.timestamp(), bid=float(self.bid), ask=float(self.ask))
        return super().symbol_info_tick(symbol)


def test_direct_and_inverse_fx_quotes_are_conservative(tmp_path: Path) -> None:
    module = runtime_module(); parent, child, state, lock = paths(tmp_path)
    direct = module.Runtime(mt5=FxMT5("USDINR.a", "USD", "INR", "84", "84.2"), parent_path=parent, child_path=child, state_path=state, lock_path=lock)
    direct.server_offset_hours = 0
    assert direct._account_to_inr_rate("USD") == 84
    inverse = module.Runtime(mt5=FxMT5("INRUSD.a", "INR", "USD", "0.0118", "0.012"), parent_path=parent, child_path=child, state_path=state, lock_path=lock)
    inverse.server_offset_hours = 0
    assert inverse._account_to_inr_rate("USD") == 1 / module.Decimal("0.012")


def test_invalid_or_unavailable_fx_quote_fails_closed(tmp_path: Path) -> None:
    module = runtime_module(); parent, child, state, lock = paths(tmp_path)
    invalid = module.Runtime(mt5=FxMT5("USDINR", "USD", "INR", "0", "0"), parent_path=parent, child_path=child, state_path=state, lock_path=lock)
    assert invalid._account_to_inr_rate("USD") is None
