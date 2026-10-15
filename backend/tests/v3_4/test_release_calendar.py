from __future__ import annotations

from datetime import UTC, datetime

from app.v3_4.providers.release_calendar import (
    can_join_at_decision,
    coverage_audit,
    default_release_registry,
    lookup_release,
    release_time_record,
)


def test_release_time_record_requires_verified_for_decision_time() -> None:
    release = datetime(2026, 8, 20, 12, 30, tzinfo=UTC)
    verified = release_time_record("BLS", "CPI", release, status="VERIFIED")
    date_only = release_time_record("ECB", "HICP", release, status="DATE_ONLY")
    unavailable = release_time_record("BEA", "GDP", None, status="UNAVAILABLE")
    assert verified["decision_time_safe"] is True
    assert date_only["decision_time_safe"] is False
    assert unavailable["decision_time_safe"] is False


def test_release_calendar_no_lookahead_join() -> None:
    release = datetime(2026, 8, 20, 12, 30, tzinfo=UTC)
    record = release_time_record("BLS", "CPI", release, status="VERIFIED")
    assert can_join_at_decision(record, datetime(2026, 8, 20, 12, 29, tzinfo=UTC)) is False
    assert can_join_at_decision(record, datetime(2026, 8, 20, 12, 31, tzinfo=UTC)) is True


def test_default_release_registry_is_fail_closed_for_partial_sources() -> None:
    registry = default_release_registry()
    assert registry["BLS:CUUR0000SA0:2026-M07"]["decision_time_safe"] is True
    assert registry["BEA:T10105:2026Q2"]["release_time_status"] == "VERIFIED"
    assert registry["ECB:FM.D.U2.EUR.4F.KR.DFR.LEV:2026-08-21"]["release_time_status"] == "DATE_ONLY"
    assert registry["ECB_POLICY_DECISION:DFR:2026-07-23"]["decision_time_safe"] is True
    assert registry["ECB:MNA.Q.Y.I9.W2.S1.S1.B.B1GQ._Z._Z._Z.EUR.LR.N:2026-Q1"]["release_time_status"] == "UNAVAILABLE"
    assert registry["EUROSTAT:GDP:2026-Q2"]["release_time_status"] == "DATE_ONLY"


def test_lookup_release_and_coverage_audit() -> None:
    cpi = lookup_release("BLS", "CUUR0000SA0", "2026-M07")
    missing = lookup_release("BEA", "UNKNOWN", "2026")
    audit = coverage_audit([cpi, missing])
    assert cpi["release_timestamp"].endswith("+00:00")
    assert missing["release_time_status"] == "UNAVAILABLE"
    assert audit["records_audited"] == 2
    assert audit["VERIFIED"] == 1
    assert audit["coverage_percentage"] == 50.0
