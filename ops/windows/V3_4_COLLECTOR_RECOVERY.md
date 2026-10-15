# V3.4 collector UTC recovery procedure

This is the canonical restart procedure for the V3.4 prospective collector on
`ICMarketsSC-Demo`. It does not start the outcome resolver.

The 2026-09-04 certification measured both EURUSD and XAUUSD current tick and
bar epochs at approximately +10,800 seconds relative to Windows/Python UTC.
The procedure therefore supplies the explicitly certified server offset; the
collector never infers an offset from tick age.

Run from an ordinary PowerShell prompt at the repository root:

```powershell
& .\ops\windows\v3-4-collector-restart.ps1 -Mode Preflight
& .\ops\windows\v3-4-collector-restart.ps1 -Mode Once
& .\ops\windows\v3-4-collector-restart.ps1 -Mode Continuous
```

The script fails if any collector process or collector lock already exists. It
loads `V3_FRED_API_KEY`, `V3_BLS_API_KEY`, and `V3_BEA_API_KEY` from the Windows
User environment without printing their values. It sets:

- `PYTHONPATH=backend`
- `APP_ENV=paper`
- `MT5_READ_ONLY=true`
- `TRADING_EXECUTION_ENABLED=false`
- `DEMO_EXECUTION_ENABLED=false`
- `REAL_EXECUTION_ENABLED=false`
- `V3_COLLECTOR_ONCE=true` for smoke or `false` for continuous mode
- `V3_COLLECTOR_CATCH_UP=false`
- `V3_MT5_SERVER_UTC_OFFSET_SECONDS=10800`

Re-certify the explicit offset after a broker server-time/DST change. Never
derive it from `current_time - latest_market_tick`, particularly while markets
are closed. A wrong or missing offset fails closed as `CLOCK_SKEW_DETECTED` or
an offset-configuration preflight error.

Continuous recovery is certified only after preflight and once mode pass. Then
verify exactly one OS process, two advancing heartbeats, advancing completed M1,
and zero future-dated rows bearing
`time_normalization_version=v3.4-mt5-explicit-offset-r1`.

The resolver remains separate. Its continuous configuration is
`V3_OUTCOME_RESOLVER_ONCE=false`, with the same paper/read-only execution flags
and provider credentials. Do not start it until a separate resolver continuity
campaign certifies its restart procedure.
