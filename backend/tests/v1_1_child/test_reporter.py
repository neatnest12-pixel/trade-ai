from decimal import Decimal

from app.v1_1_child.contract import HASH, PARENT_CONFIG, PARENT_SNAPSHOT, VERSION
from app.v1_1_child.reporter import data_quality, official_contracts, report, sample_gate, verdict


def contract() -> dict[str, object]:
    return {"record_type": "TRADE_CONTRACT", "trade_contract_version": VERSION, "trade_contract_hash": HASH,
            "parent_prediction_id": "p", "parent_v1_snapshot_id": PARENT_SNAPSHOT, "parent_v1_config_hash": PARENT_CONFIG,
            "paper_trade_id": "t"}


def outcome(net: str, side: str = "BUY") -> dict[str, object]:
    return {"record_type": "RR_OUTCOME", "paper_trade_id": "t", "rr_profile": "RR_2", "cost_profile": "BASE",
            "net_r": net, "gross_r": net, "cost_r": "0", "outcome": "TP_HIT" if Decimal(net) > 0 else "SL_HIT", "decision": side}


def test_empty_and_mixed_metrics() -> None:
    assert report([], "RR_2", "BASE")["status"] == "NOT_ENOUGH_DATA"
    metrics = report([contract(), outcome("2"), outcome("-1")], "RR_2", "BASE")
    assert metrics["expectancy"] == Decimal("0.5")
    assert metrics["profit_factor"] == Decimal("2")
    assert metrics["ending_simulated_balance"] == Decimal("2525")
    assert metrics["max_drawdown_inr"] == Decimal("25")


def test_official_filter_quality_gates_and_verdict() -> None:
    rows = [contract(), outcome("2"), {**contract(), "trade_contract_hash": "wrong"}]
    assert len(official_contracts(rows)) == 1
    assert data_quality(rows)["orphan_outcomes"] == 0
    assert sample_gate(19) == "INSUFFICIENT" and sample_gate(100) == "MEANINGFUL_FORWARD_SAMPLE"
    assert verdict(report([contract(), outcome("2")], "RR_2", "BASE")) == "INSUFFICIENT_DATA"
