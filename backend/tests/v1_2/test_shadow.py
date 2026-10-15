from decimal import Decimal
from pathlib import Path

import pytest

from app.v1_2.shadow import (
    PROSPECTIVE_START,
    ShadowJournalError,
    append,
    branch_state,
    contract,
    eligible,
    priced,
    read,
)


def parent(side="BUY", timestamp="2026-08-19T05:16:00+00:00"):
    return {
        "type": "prediction",
        "record_hash": "parent-1",
        "v1_snapshot_id": "v1-paper-r4-0d891a2356e35464",
        "v1_config_hash": "0d891a2356e35464dbe3ba9f51c50fe240e40feff84fe4008d8b1564b55f9eac",
        "decision": side,
        "candle_timestamp": timestamp,
    }


def candles():
    return [
        {
            "timestamp": f"2026-08-19T{5 + i // 60:02}:{17 + i % 60:02}:00+00:00",
            "open": "100",
            "high": "103",
            "low": "99",
            "close": "101",
        }
        for i in range(60)
    ]


def test_boundary_parent_filter_and_contract() -> None:
    assert (
        eligible([parent(timestamp=PROSPECTIVE_START), {**parent(), "decision": "NO_TRADE"}]) == []
    )
    c = contract(parent("SELL"), Decimal("100"), Decimal("1"), Decimal(".1"), {"point": ".00001"})
    assert (
        c["decision"] == "SELL"
        and c["stop_price"] == Decimal("101.5")
        and c["entry_timestamp"] == "NEXT_M1"
    )


def test_cost_matrix_and_twelve_branches() -> None:
    c = contract(parent(), Decimal("100"), Decimal("1"), Decimal(".1"), {})
    outcomes = [
        priced(c, rr, cost, candles())
        for rr in ("RR_2", "RR_3", "RR_5", "RR_10")
        for cost in ("IDEALIZED", "BASE", "STRESS")
    ]
    assert len(outcomes) == 12 and all(
        x and {"gross_r", "cost_r", "net_r"} <= x.keys() for x in outcomes
    )


def test_append_dedup_and_recovery_state(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    c = contract(parent(), Decimal("100"), Decimal("1"), Decimal(".1"), {})
    c.update({"created_at": "x"})
    assert append(c, path) and not append(c, path)
    outcome = {
        "record_type": "SHADOW_OUTCOME",
        "created_at": "x",
        "shadow_trade_id": c["shadow_trade_id"],
        "profile": "PROFILE_A",
        "rr_profile": "RR_2",
        "cost_profile": "IDEALIZED",
        "net_r": "2",
        "gross_r": "2",
        "exit_timestamp": "x",
    }
    assert append(outcome, path) and branch_state(
        read(path), "PROFILE_A", "RR_2", "IDEALIZED"
    ).balance == Decimal("50250.0000")
    path.write_text("bad\n")
    with pytest.raises(ShadowJournalError):
        read(path)
