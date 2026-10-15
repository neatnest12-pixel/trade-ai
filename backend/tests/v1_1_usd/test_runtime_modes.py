from __future__ import annotations

import importlib.util
from pathlib import Path


def module():
    path = Path(__file__).parents[2] / "scripts" / "v1_1_usd_paper_trade_processor.py"
    spec = importlib.util.spec_from_file_location("usd_runtime_modes", path)
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


def test_main_parses_explicit_once_values(monkeypatch) -> None:
    runtime = module()
    calls = []
    class Fake:
        def run(self, once: bool) -> int: calls.append(once); return 0
    monkeypatch.setattr(runtime, "Runtime", Fake)
    for value, expected in (("true", True), ("1", False), ("false", False), ("0", False)):
        monkeypatch.setenv("V1_1_USD_PROCESSOR_ONCE", value)
        assert runtime.main() == 0 and calls.pop() is expected


def test_false_enters_continuous_loop_until_explicit_stop(monkeypatch) -> None:
    runtime_module = module(); runtime = runtime_module.Runtime(mt5=type("Mt5", (), {"shutdown": lambda self: None})())
    class Lock:
        def acquire(self): return True
        def refresh(self): return True
        def release(self): return True
    runtime.lock = Lock(); runtime.preflight = lambda: None; runtime.state = lambda *_args, **_kwargs: None
    cycles = []
    runtime.cycle = lambda: cycles.append(True)
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _seconds: setattr(runtime, "stop", True))
    assert runtime.run(once=False) == 0
    assert len(cycles) == 1
