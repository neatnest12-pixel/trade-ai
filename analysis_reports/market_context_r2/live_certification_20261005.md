# EURUSD shadow context certification — 2026-10-05

Context version: `v3.5-broker-context-r2`.

## Measured live evidence

Read-only MT5 measurement at 2026-10-05T02:57:13.823444Z:

- Existing terminal PID 836; authenticated ICMarketsSC-Demo, demo account mode 0.
- EURUSD visible; bid 1.11740, ask 1.11741; spread 0.10 pip.
- Raw tick epoch 1791179833.304; existing configured normalization subtracts 10800 seconds.
- Normalized timestamp 2026-10-05T02:57:13.304Z; age 0.519444 seconds; two samples advanced.
- Session ASIA_ONLY; active ASIA; next ASIA_LONDON_TRANSITION.
- Nominal OPEN, broker schedule UNKNOWN, effective OPEN from fresh advancing authenticated quotes.
- Feed HEALTHY_LIVE_FEED. Spread/volatility states UNKNOWN: no qualifying reference samples supplied.
- Independent clock certification PASS; time.google.com, mean offset -0.0185003 seconds.

Schedule authority: GENERIC_TEMPLATE. Installed MetaTrader5 Python 5.0.6090 has no exposed session schedule functions. Existing local-time weekday templates are not broker-certified hours. Symbol session_open/session_close fields describe prices and do not establish weekly hours. No broker holiday/maintenance evidence was acquired. Fresh quotes establish current activity, not a future trading schedule.

## Recorder and integration

Separate store: runtime/v35_context_r2/context.sqlite3. Two LIVE_DIAGNOSTIC records, one REPLAY record, zero PROSPECTIVE records. Diagnostic records have no observation identity and are independently identified by measurement time. Duplicate observation/version checks exclude null observation IDs; both those and duplicate record-ID checks return zero.

Store quick_check is ok; foreign_key_check returns no violations (there are no campaign foreign-key references). UPDATE and DELETE triggers are present. Disposable fixture tests demonstrate mutation rejection; real evidence was not mutated to test protection.

Continuous collection has a post-commit shadow hook for new eligible observations. No return value affects decisions. Context failures are caught, reported to stdout and isolated from campaign writes. The hook uses committed evidence only, with the certified decision timestamp explicitly documented as a conservative availability upper bound, not an exact acquisition timestamp. Terminal connection/advancement evidence absent from the snapshot remains unknown.

Tests cover idempotency, unavailable recorder, malformed context, unknown authority, missing spread/volatility references, future evidence rejection, input preservation, fixture path protection, DST transitions and mismatched US/UK DST weeks. Fixture stores cannot target any path beneath the project root.

## Immutable evidence recertification

Both campaign databases: quick_check ok; no foreign-key violations; existing append-only triggers present. No duplicate observation/setup/outcome identities found.

- Existing observations: 462; fingerprint d09c4c25304e77686a9f453223388339de1a16425fbba8a0841937bc598a18da — SAME.
- Admitted setups: 263; fingerprint 0cc6395e10059445a409867b0ed18483671dbabee41ed38a7d6af0d0f9f66a0e — SAME.
- Outcomes: 212; fingerprint 7e14dfc1a067931204bf2f2c4a4963ebade8247ff001ba9c719d0a4bd0fae050 — SAME.
- Frozen V3.4: 52/52 match existing manifest; combined fingerprint fad7d0c56691fc980a96b4d49feb10cb16341e317d4bd23a06deab735e5c41f7 — SAME.

Results: 128 wins, 84 losses, zero breakeven; win rate 60.37735849%; average win +1.033777275R, average loss -1.012389222R; gross expectancy +0.223032059R; gross profit factor 1.556002004.

Admitted pending count directly queried by anti-join: 51. Historical 54 represents 266 directional observations minus 212 outcomes; three directional observations are not admitted setup identities. New resolved since 212: zero; remaining to 250: 38.

## Recovery gate and certification decision

C: measured free 30,010,109,952 bytes (30.010 GB), total 255,363,067,904 bytes; disk guardrail and 30 GB target pass at measurement, with minimal margin. No cleanup performed.

Read-only existing worker system preflight FAILED: STALE_EURUSD_BAR:H12:194430.240. The existing completed H12 bar is approximately 54 hours old, beyond the existing two-bar freshness limit. Clock validation was executed without lifecycle logging; no symbol selection was performed because EURUSD was already visible. No gate was weakened.

Collector recorded PID 15676 absent, saved RUNNING state/lock with October 1 heartbeat; resolver recorded PID 15536 absent, saved FAILED state and prior disk error, active resolver lock absent. Locks/state were not changed. No workers were started.

Final V3.5 suite: 157 passed, zero failures. An intermediate newly added test failed on a Windows fixture cleanup handle; corrected by explicitly closing its SQLite connection, then the complete suite passed.

LEVEL 3 IMPLEMENTATION: PASS for tested context/shadow implementation with explicit schedule uncertainty.
LEVEL 3 LIVE CERTIFICATION: PENDING; no real prospective observation record.
SUPPORTED LEVEL: LEVEL 2 overall live certification; LEVEL 3 implementation candidate only. No Level 4 integration, signal filtering, risk changes or profitability rules.

Training, paper/demo/real trading and orders: not started/placed. Next action: read-only recheck of completed H12 data and disk margin; when existing recovery gates pass, exercise the established collector procedure for minimum prospective context evidence and recertify existing identity subsets. Do not reset outcomes or loosen bar freshness.
