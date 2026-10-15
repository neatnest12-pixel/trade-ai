# Market context r1 — offline, informational layer

This additive module is deliberately disconnected from collectors, resolvers,
strategy decisions, frozen feature manifests, training admission and execution.
No schema changes or migrations are needed. Existing features are not replaced.

`context(aware_timestamp, symbol, ...)` is deterministic and uses UTC internally.
Naive timestamps fail. Source and broker timestamps are retained separately;
broker timestamps must be normalized by the existing adapter before calling it.
Weekday is UTC Monday=0. ISO week year is separate from calendar year.

Session conventions: Tokyo 09:00–18:00, London 08:00–17:00 and New York
08:00–17:00 on their local weekdays. These are analytical windows, not universal
exchange schedules or profitable-session claims. IANA ZoneInfo handles DST.
Missing timezone data fails; never substitute fixed offsets. Maintain the host's
timezone data through normal dependency management before future deployment.

Calendar templates for EURUSD, XAUUSD and XAGUSD are UNVERIFIED. Consequently
`is_market_open` is null until a separately configured InstrumentSchedule has
`verified=True` and an evidence reference. `nominal_market_open` is indicative.
Broker holidays, exceptional closures and maintenance must be supplied from
point-in-time broker evidence; metal templates are not a CME schedule assertion.
No calendar should be retrospectively replaced with information unknown at T.

Open/close, transition, rollover and weekly windows are Config fields. Active
session flags may overlap; `current_session` is exclusive. Session age is time
since the most recently opened active named session, and close time is time to
the earliest active named-session close. Previous/next refer to contextual state
changes. Known scheduled future boundaries are calendar metadata, not future
price observations. Configurations must be selected as known at observation T.

Spread references are instrument- and exclusive-session-specific, timestamped
strictly before T and available by T. Current quotes require both quote and
availability timestamps <= T. Volatility is unannualized log-return population
standard deviation per supplied bar frequency; ATR is a simple mean true range
over the configured period, not Wilder smoothing. Callers must supply one
consistent bar timeframe and sampling frequency, with complete closed bars.
The configured bar_interval_seconds (default 60) must match contiguous closes;
gaps or a mismatched cadence produce null ATR/realized volatility.
Relative baselines require minimum samples; absent data yields UNKNOWN/null.
Percentile thresholds describe evidence, not order eligibility. No universal
instrument price thresholds or liquidity rankings are assumed.

`status_view(features)` exposes an informational monitoring payload. There is
no live UI wiring: adding it to monitoring/collection needs separate validation,
configuration provenance and a new feature-manifest version.

The offline report command accepts the certified sidecar read-only and only
creates a new report under analysis_reports/market_context_r1. It refuses to
overwrite an existing report. Membership session groups overlap, while exclusive
session groups partition results. Reports use decision timestamps, never exit
timestamps, to assign sessions. Missing historical spread/volatility stay UNKNOWN.
Samples below 30 are flagged; counts are not independent effective sample sizes.
No significance, profitability, net returns or training readiness is inferred.

Test with PYTHONDONTWRITEBYTECODE=1 and unittest discovery; test databases live
only in temporary directories and never point to campaign data.
