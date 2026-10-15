import json
from pathlib import Path

import pytest

from app.v1_1_child import journal


def record(record_type: str = "TRADE_CONTRACT", **values: object) -> dict[str, object]:
    return {
        "record_type": record_type,
        "created_at": "2026-08-18T00:00:00Z",
        **values,
    }


@pytest.fixture
def journal_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "certified_data" / "PAPER_TRADE_JOURNAL.jsonl"
    monkeypatch.setattr(journal, "JOURNAL_PATH", path)
    return path


def test_append_is_byte_preserving_and_one_line_per_record(journal_path: Path) -> None:
    journal.append_record(record(parent_prediction_id="a", trade_contract_hash="one"))
    first_line = journal_path.read_bytes()
    journal.append_record(record("SYSTEM_EVENT", event="second"))

    lines = journal_path.read_bytes().splitlines(keepends=True)
    assert len(lines) == 2
    assert lines[0] == first_line
    assert lines[1].endswith(b"\n")


def test_malformed_json_fails_closed(journal_path: Path) -> None:
    journal_path.parent.mkdir(parents=True)
    journal_path.write_text('{"record_type":\n', encoding="utf-8")

    with pytest.raises(journal.PaperTradeJournalError, match="MALFORMED_PAPER_TRADE_JOURNAL"):
        journal.read_records()


def test_duplicate_json_keys_fail_closed(journal_path: Path) -> None:
    journal_path.parent.mkdir(parents=True)
    journal_path.write_text(
        '{"record_type":"SYSTEM_EVENT","record_type":"TRADE_SKIPPED"}\n', encoding="utf-8"
    )

    with pytest.raises(journal.PaperTradeJournalError):
        journal.read_records()


def test_hash_is_deterministic_and_excludes_its_own_field() -> None:
    first = record(event="same", nested={"b": 2, "a": 1})
    equivalent = json.loads(json.dumps(first))
    different = record(event="different", nested={"a": 1, "b": 2})

    assert journal.record_digest(first) == journal.record_digest(equivalent)
    assert journal.record_digest(first) != journal.record_digest(different)
    first["record_hash"] = "not-part-of-the-digest"
    assert journal.record_digest(first) == journal.record_digest(equivalent)


def test_contract_dedup_uses_only_contract_identity(journal_path: Path) -> None:
    assert not journal.contract_exists("parent-a", "contract-a")
    journal.append_record(record(parent_prediction_id="parent-a", trade_contract_hash="contract-a"))

    assert journal.contract_exists("parent-a", "contract-a")
    assert not journal.contract_exists("parent-a", "contract-b")
    assert not journal.contract_exists("parent-b", "contract-a")


def test_rr_outcome_dedup_uses_only_outcome_identity(journal_path: Path) -> None:
    assert not journal.rr_outcome_exists("trade-a", "RR_2", "BASE")
    journal.append_record(
        record("RR_OUTCOME", paper_trade_id="trade-a", rr_profile="RR_2", cost_profile="BASE")
    )

    assert journal.rr_outcome_exists("trade-a", "RR_2", "BASE")
    assert not journal.rr_outcome_exists("trade-a", "RR_3", "BASE")
    assert not journal.rr_outcome_exists("trade-a", "RR_2", "STRESS")


def test_read_empty_or_missing_journal_is_empty(journal_path: Path) -> None:
    assert journal.read_records() == []
    journal_path.parent.mkdir(parents=True)
    journal_path.write_text("\n\n", encoding="utf-8")
    assert journal.read_records() == []
