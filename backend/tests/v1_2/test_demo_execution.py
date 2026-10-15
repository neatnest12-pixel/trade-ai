from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.v1_2.demo_execution import (
    CAMPAIGN_ID,
    CONFIG_HASH,
    DEMO_MODE,
    REAL_MODE,
    SNAPSHOT_ID,
    DemoOrderWrapper,
    ExecutionBlocked,
    account_eligibility,
    prepare_order,
)

DEMO = SimpleNamespace(trade_mode=DEMO_MODE, company="Raw Trading Ltd", server="ICMarketsSC-Demo")
REAL = SimpleNamespace(trade_mode=REAL_MODE, company="Raw Trading Ltd", server="ICMarketsSC-Demo")


def test_identity_and_account_modes():
    assert CAMPAIGN_ID == "NAVORA-QUANT-V1.2-DEMO-PREP" and SNAPSHOT_ID.startswith("v1.2-demo-")
    assert len(CONFIG_HASH) == 64 and account_eligibility(DEMO) and not account_eligibility(REAL)
    with pytest.raises(ExecutionBlocked):
        DemoOrderWrapper(object()).prepare(REAL, prepare_order(signal_id="x", decision="BUY", entry=Decimal("1.1"), stop_distance=Decimal(".001"), balance=Decimal("2500")))


def test_missing_or_unexpected_account_fails_closed():
    assert not account_eligibility(None)
    assert not account_eligibility(SimpleNamespace(trade_mode=DEMO_MODE, company="Other", server="ICMarketsSC-Demo"))
    assert not account_eligibility(SimpleNamespace(trade_mode=DEMO_MODE, company="Raw Trading Ltd", server="Other"))


def test_risk_and_order_plans():
    buy = prepare_order(signal_id="b", decision="BUY", entry=Decimal("1.1"), stop_distance=Decimal(".001"), balance=Decimal("100000"))
    sell = prepare_order(signal_id="s", decision="SELL", entry=Decimal("1.1"), stop_distance=Decimal(".001"), balance=Decimal("2500"))
    no = prepare_order(signal_id="n", decision="NO_TRADE", entry=Decimal("1.1"), stop_distance=Decimal(".001"), balance=Decimal("2500"))
    assert buy.risk_rupees == Decimal("25") and buy.expected_rr == Decimal("2")
    assert sell.stop > sell.entry > sell.target and no.entry is None


def test_duplicate_and_one_position_guard():
    wrapper = DemoOrderWrapper(object())
    plan = prepare_order(signal_id="x", decision="BUY", entry=Decimal("1.1"), stop_distance=Decimal(".001"), balance=Decimal("2500"))
    wrapper.mark_prepared("x")
    with pytest.raises(ExecutionBlocked):
        wrapper.mark_prepared("x")
    with pytest.raises(ExecutionBlocked):
        wrapper.prepare(DEMO, plan)


def test_demo_prepare_has_no_submission():
    wrapper = DemoOrderWrapper(object())
    result = wrapper.prepare(DEMO, prepare_order(signal_id="x", decision="SELL", entry=Decimal("1.1"), stop_distance=Decimal(".001"), balance=Decimal("2500")))
    assert result["action"] == "PREPARED_ONLY"

