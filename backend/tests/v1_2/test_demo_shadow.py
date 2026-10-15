from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.v1_2.demo_shadow import (
    HASH,
    RISK_PROFILES,
    STARTING_BALANCE_USD,
    VERSION,
    DemoSafetyBlocked,
    ShadowState,
    apply_net_r,
    guard,
    require_demo_usd,
    risk_usd,
)


def test_identity_and_risks() -> None:
    state = ShadowState(); assert VERSION == "v1.2-demo-50k-01" and len(HASH) == 64 and STARTING_BALANCE_USD == 50000
    assert [risk_usd(state, x) for x in RISK_PROFILES] == [Decimal("125.0000"), Decimal("250.000"), Decimal("500.00")]


def test_compounding_without_martingale() -> None:
    win, result = apply_net_r(ShadowState(), "PROFILE_B", Decimal("2")); assert result["risk_usd"] == 250 and win.balance == 50500
    loss, result = apply_net_r(win, "PROFILE_B", Decimal("-1")); assert result["risk_usd"] == Decimal("252.500") and loss.balance == Decimal("50247.500")


def test_loss_and_drawdown_guards() -> None:
    assert guard(ShadowState(consecutive_losses=5)) == "CONSECUTIVE_LOSS_LIMIT_REACHED"
    daily = ShadowState(balance=Decimal("49000"), daily_loss=Decimal("1000")); assert guard(daily) == "DAILY_LOSS_LIMIT_REACHED"
    warning = ShadowState(balance=Decimal("47500"), peak=Decimal("50000")); assert guard(warning) is None
    paused = ShadowState(balance=Decimal("45000"), peak=Decimal("50000")); assert guard(paused) == "DRAWDOWN_HARD_PAUSE"
    review, result = apply_net_r(ShadowState(), "PROFILE_C", Decimal("-15"))
    assert review.review_required and result["drawdown_state"] == "REVIEW"


def test_demo_only_hard_block() -> None:
    require_demo_usd(SimpleNamespace(trade_mode=0, server="ICMarketsSC-Demo", currency="USD"))
    with pytest.raises(DemoSafetyBlocked, match="REAL_ACCOUNT_EXECUTION_BLOCKED"): require_demo_usd(SimpleNamespace(trade_mode=2, server="ICMarketsSC-Demo", currency="USD"))
