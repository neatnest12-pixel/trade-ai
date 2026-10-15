# Broker context r2

EURUSD-only, pure, shadow-mode API in broker_context.py. No live collector,
resolver, strategy, risk, account configuration or outcome-store changes.
The existing <=5-second freshness threshold remains fixed.

CalendarEvidence describes one broker assertion for a bounded UTC interval,
with source, broker/server identity and availability time. Authoritative=True
must be set only by a separately audited broker evidence adapter/operator.
The module does not independently authenticate a source string. Generic forex
templates, weekday and London activity never establish broker authority.
Live advancing fresh quotes can demonstrate OPEN without certifying a schedule.
Contradictory closed-calendar/fresh-quote evidence returns UNKNOWN.

TerminalEvidence requires recent measurements, authenticated expected server,
clock safety, symbol visibility/availability and initialization/connection.
Tick advancement needs two samples in acquisition order. Fresh means <=5 seconds.
Calendar-supported closure and open-market stagnant/stale feed have distinct
diagnoses. The latter is diagnostic suspicion, not proof of a network fault.
Absent broker calendar plus stale Sunday tick returns INSUFFICIENT_EVIDENCE.

Caller-supplied evidence must be normalized and available by observation T.
Future/late-available reference samples, bars, calendars and acquisition times
raise FutureEvidenceError. A future broker tick itself yields a clock/timestamp
diagnosis. The API has no outcome input and no strategy decision input/output.
Config and calendar revisions must be selected as known at T.

shadow_observation constructs one versioned snapshot and optionally delivers it
to an explicit context-only recorder. The continuous collector now has a
post-commit observe_committed hook for new eligible observations. It writes only
runtime/v35_context_r2/context.sqlite3, never campaign stores. Recorder failures
are caught and reported to stdout; no return value enters decision logic.
The hook uses committed timestamp/quote evidence, with the certified decision
timestamp as a conservative availability upper bound. Terminal connectivity and
tick advancement are unavailable in that snapshot and remain unknown.
No worker has been started to exercise this hook against a new live observation.
Disposable fixtures verify UPDATE/DELETE rejection, malformed input isolation,
missing references, unknown authority, and idempotency.
status_view is an executable read-only presentation adapter.

Spread and volatility reuse instrument/session-specific historical references.
Insufficient history remains UNKNOWN. No outcome-based threshold tuning occurs.
Existing historical report remains immutable; the new report adds warnings for
multiple comparisons and temporal clustering. Membership groups overlap.

Live read-only diagnostics can be recorded separately from prospective samples.
The installed MT5 Python integration exposes no session schedule function;
symbol session_open/session_close price fields do not certify weekly hours.
Schedule authority remains GENERIC_TEMPLATE. Live broker schedule acquisition,
holidays and maintenance remain unverified. A real post-commit prospective
record is still required for full live certification. No level 4 claim is supported.
