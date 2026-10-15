from backend.scripts.v3_prospective_collector import existing_observation_identities


def test_dedup_identity_ignores_policy_hash_for_same_m1_timestamp() -> None:
    rows = [
        {
            "symbol": "EURUSD",
            "timestamp": "2026-08-20T10:00:00+00:00",
            "strategy_input_policy_hash": "old",
        },
        {
            "symbol": "EURUSD",
            "timestamp": "2026-08-20T10:00:00+00:00",
            "strategy_input_policy_hash": "new",
        },
    ]

    assert existing_observation_identities(rows) == {("EURUSD", "2026-08-20T10:00:00+00:00")}
