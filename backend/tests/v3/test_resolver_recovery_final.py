import json
import sys
from types import SimpleNamespace

import pytest

from scripts import v34_recertify as runner


def test_success_after_retry_clears_error_and_persists_cycle(tmp_path, monkeypatch):
    from app.v3 import prospective, worker_audit
    from scripts import v34_prospective_campaign
    monkeypatch.setattr(worker_audit, 'LOGS', tmp_path / 'logs')
    monkeypatch.setattr(prospective, 'MANIFEST', tmp_path / 'absent')
    monkeypatch.setattr(runner, 'require_safety', lambda: None)
    for name, value in {'CAMPAIGN': tmp_path / 'campaign', 'DB': tmp_path / 'test.sqlite3',
                        'STATE': tmp_path / 'state.json', 'LOCK': tmp_path / 'lock'}.items():
        monkeypatch.setattr(runner, name, value)
    monkeypatch.setitem(sys.modules, 'MetaTrader5', SimpleNamespace(
        account_info=lambda: SimpleNamespace(server='ICMarketsSC-Demo', trade_mode=0, currency='USD')))
    monkeypatch.setattr(runner, 'initialize_checked', lambda _: True)
    monkeypatch.setattr(runner, 'shutdown_mt5', lambda _: None)
    monkeypatch.setattr(runner, 'MT5Ticks', lambda *args: None)
    monkeypatch.setattr(runner, 'ingest', lambda *args: None)
    monkeypatch.setattr(runner, 'commit_candidates', lambda *args: None)
    monkeypatch.setattr(prospective, 'attach_labels', lambda *args: None)
    monkeypatch.setattr(v34_prospective_campaign, 'health', lambda **kwargs: None)
    attempts = []
    def feasibility(*args):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError('temporary outage')
    monkeypatch.setattr(runner, 'feasibility', feasibility)
    snapshots = []
    def sleep(_):
        snapshots.append(json.loads(runner.STATE.read_text()))
        if len(snapshots) == 2:
            raise KeyboardInterrupt()
    monkeypatch.setattr(runner.time, 'sleep', sleep)
    with pytest.raises(KeyboardInterrupt):
        runner.main(['serve'])
    assert snapshots[0]['status'] == 'RETRY_WAIT'
    assert 'temporary outage' in snapshots[0]['last_error']
    assert snapshots[1]['status'] == 'RUNNING'
    assert snapshots[1]['last_error'] is None
    assert snapshots[1]['last_successful_cycle']
    assert not runner.LOCK.exists()
