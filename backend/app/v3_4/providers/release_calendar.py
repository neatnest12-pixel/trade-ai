from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo


def release_time_record(
    provider: str,
    series_id: str,
    release_timestamp: datetime | None,
    *,
    status: str,
    observation_period: str | None = None,
    source: str | None = None,
    quality: str = "VERIFIED",
    vintage: str | None = None,
) -> dict[str, Any]:
    if status not in {"VERIFIED", "DATE_ONLY", "UNAVAILABLE"}:
        raise ValueError("invalid release time status")
    return {
        "provider": provider,
        "series_id": series_id,
        "observation_period": observation_period,
        "release_timestamp": release_timestamp.astimezone(UTC).isoformat() if release_timestamp else None,
        "release_time_status": status,
        "release_source": source,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "quality": quality if status != "UNAVAILABLE" else "UNAVAILABLE",
        "vintage": vintage,
        "decision_time_safe": status == "VERIFIED" and release_timestamp is not None,
    }


def can_join_at_decision(record: dict[str, Any], decision_timestamp: datetime) -> bool:
    if not record.get("decision_time_safe"):
        return False
    release_timestamp = record.get("release_timestamp")
    if not release_timestamp:
        return False
    release = datetime.fromisoformat(str(release_timestamp))
    if release.tzinfo is None:
        release = release.replace(tzinfo=UTC)
    return release <= decision_timestamp.astimezone(UTC)


def default_release_registry() -> dict[str, dict[str, Any]]:
    eastern = ZoneInfo("America/New_York")
    cet = ZoneInfo("Europe/Brussels")
    return {
        "BLS:CUUR0000SA0:2026-M07": release_time_record("BLS", "CUUR0000SA0", datetime(2026, 8, 12, 8, 30, tzinfo=eastern), status="VERIFIED", observation_period="2026-M07", source="https://www.bls.gov/schedule/news_release/cpi.htm"),
        "BLS:CES0000000001:2026-M07": release_time_record("BLS", "CES0000000001", datetime(2026, 8, 7, 8, 30, tzinfo=eastern), status="VERIFIED", observation_period="2026-M07", source="https://www.bls.gov/schedule/news_release/empsit.htm"),
        "BLS:LNS14000000:2026-M07": release_time_record("BLS", "LNS14000000", datetime(2026, 8, 7, 8, 30, tzinfo=eastern), status="VERIFIED", observation_period="2026-M07", source="https://www.bls.gov/schedule/news_release/empsit.htm"),
        "BLS:WPUFD4:2026-M07": release_time_record("BLS", "WPUFD4", datetime(2026, 8, 13, 8, 30, tzinfo=eastern), status="VERIFIED", observation_period="2026-M07", source="https://www.bls.gov/schedule/news_release/ppi.htm"),
        "BEA:T10105:2026Q2": release_time_record("BEA", "T10105", datetime(2026, 7, 30, 8, 30, tzinfo=eastern), status="VERIFIED", observation_period="2026Q2", source="https://www.bea.gov/news/schedule/full", vintage="ADVANCE"),
        "BEA:T10106:2026Q2": release_time_record("BEA", "T10106", datetime(2026, 7, 30, 8, 30, tzinfo=eastern), status="VERIFIED", observation_period="2026Q2", source="https://www.bea.gov/news/schedule/full", vintage="ADVANCE"),
        "BEA:T20804:2026M06": release_time_record("BEA", "T20804", datetime(2026, 7, 30, 8, 30, tzinfo=eastern), status="VERIFIED", observation_period="2026M06", source="https://www.bea.gov/news/schedule/full"),
        "BEA:T20600:2026M06": release_time_record("BEA", "T20600", datetime(2026, 7, 30, 8, 30, tzinfo=eastern), status="VERIFIED", observation_period="2026M06", source="https://www.bea.gov/news/schedule/full"),
        "ECB:FM.D.U2.EUR.4F.KR.DFR.LEV:2026-08-21": release_time_record("ECB", "FM.D.U2.EUR.4F.KR.DFR.LEV", None, status="DATE_ONLY", observation_period="2026-08-21", source="https://data.ecb.europa.eu/data/datasets/FM/FM.D.U2.EUR.4F.KR.DFR.LEV"),
        "ECB_POLICY_DECISION:DFR:2026-07-23": release_time_record("ECB", "DFR", datetime(2026, 7, 23, 14, 15, tzinfo=cet), status="VERIFIED", observation_period="2026-07-23", source="https://www.ecb.europa.eu/press/govcdec/mopo/html/index.en.html"),
        "ECB:ICP.M.U2.N.000000.4.ANR:2025-12": release_time_record("ECB", "ICP.M.U2.N.000000.4.ANR", datetime(2026, 1, 19, 12, 0, tzinfo=cet), status="VERIFIED", observation_period="2025-12", source="https://www.ecb.europa.eu/press/calendars/statscal/ges/html/sthicp.en.html"),
        "ECB:ICP.M.U2.N.XEF000.4.ANR:2025-12": release_time_record("ECB", "ICP.M.U2.N.XEF000.4.ANR", datetime(2026, 1, 19, 12, 0, tzinfo=cet), status="VERIFIED", observation_period="2025-12", source="https://www.ecb.europa.eu/press/calendars/statscal/ges/html/sthicp.en.html"),
        "ECB:MNA.Q.Y.I9.W2.S1.S1.B.B1GQ._Z._Z._Z.EUR.LR.N:2026-Q1": release_time_record("ECB", "MNA.Q.Y.I9.W2.S1.S1.B.B1GQ._Z._Z._Z.EUR.LR.N", None, status="UNAVAILABLE", observation_period="2026-Q1", source="https://data.ecb.europa.eu/help/api/overview"),
        "EUROSTAT:GDP:2026-Q2": release_time_record("EUROSTAT", "GDP", None, status="DATE_ONLY", observation_period="2026-Q2", source="https://ec.europa.eu/eurostat/news/release-calendar"),
    }


def registry_key(provider: str, series_id: str, observation_period: str) -> str:
    return f"{provider}:{series_id}:{observation_period}"


def lookup_release(provider: str, series_id: str, observation_period: str, registry: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    return (registry or default_release_registry()).get(
        registry_key(provider, series_id, observation_period),
        release_time_record(provider, series_id, None, status="UNAVAILABLE", observation_period=observation_period),
    )


def coverage_audit(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    counts = {status: sum(1 for record in records if record.get("release_time_status") == status) for status in ("VERIFIED", "DATE_ONLY", "UNAVAILABLE")}
    return {
        "records_audited": total,
        **counts,
        "coverage_percentage": 0.0 if total == 0 else round((counts["VERIFIED"] / total) * 100, 2),
    }
