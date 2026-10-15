from decimal import Decimal

from app.v1_1_usd.outcome_costs import is_legacy_missing_cost, price_outcome
from app.v1_1_usd.reporter import official_net_outcomes


def test_idealized_and_usd_pnl() -> None:
    result = price_outcome({"gross_r": "2"}, "IDEALIZED", None, "0.001")
    assert result == {"gross_r": Decimal("2"), "spread_cost_r": Decimal("0"), "fixed_cost_r": Decimal("0"), "cost_r": Decimal("0"), "net_r": Decimal("2"), "simulated_pnl_usd": Decimal("0.60")}


def test_base_and_stress_are_conservative() -> None:
    base = price_outcome({"gross_r": "-1"}, "BASE", "0.0001", "0.001")
    stress = price_outcome({"gross_r": "-1"}, "STRESS", "0.0001", "0.001")
    assert base and base["cost_r"] == Decimal("0.15") and base["net_r"] == Decimal("-1.15")
    assert stress and stress["cost_r"] == Decimal("0.30") and stress["net_r"] == Decimal("-1.30")


def test_missing_spread_fails_closed_and_legacy_is_identified() -> None:
    assert price_outcome({"gross_r": "2"}, "BASE", None, "0.001") is None
    assert price_outcome({"gross_r": "2"}, "STRESS", None, "0.001") is None
    assert is_legacy_missing_cost({"record_type": "RR_OUTCOME", "gross_r": "1"})
    rows = [{"record_type": "RR_OUTCOME", "gross_r": "1"}, {"record_type": "RR_OUTCOME", "gross_r": "1", "spread_cost_r": "0", "fixed_cost_r": "0", "cost_r": "0", "net_r": "1", "simulated_pnl_usd": "0.30"}]
    assert official_net_outcomes(rows) == [rows[1]]
