# Navora Quant AI Repository Audit

Audit date: 2026-08-13. Scope: all backend, frontend, tests, Docker files and dependencies present
in the repository. This audit distinguishes implemented mechanics from empirically validated edge.

## Architecture inventory

The backend is a FastAPI/SQLAlchemy service with market-data adapters, pandas-based indicators and
features, deterministic strategy agents, scikit-learn model helpers, a bar backtester, risk sizing,
prediction/paper-trade persistence, and SQLite/PostgreSQL configuration. The frontend is a Next.js
App Router client using Lightweight Charts and Tailwind. Docker Compose supplies PostgreSQL, API,
and web services.

## CRITICAL

- **Future leakage in structure:** centered rolling windows originally labeled a pivot at the pivot
  timestamp using future bars. Fixed: a pivot is now emitted only on its confirmation candle, with
  the pivot price retained separately. Prefix-invariance tests prove earlier features do not change.
- **Future leakage in order blocks:** `shift(-1)` used the next candle. Fixed: candidates are emitted
  on the confirming displacement candle and refer only to the prior candle's price.
- **Mock data masqueraded as configured data:** the API always constructed `MockAdapter` and labeled
  the response mock regardless of configuration. Fixed: adapter selection honors configuration and
  mock data is rejected outside development/test. Mock results are never accepted as validation.

## HIGH

- **Arbitrary probabilities/confidence:** production responses were a hand-weighted agent average,
  with hardcoded confidence. Fixed defensively: heuristic confidence is now LOW/0 with
  `INSUFFICIENT_EVIDENCE`, and every heuristic output is NO TRADE until calibrated evidence exists.
- **No immutable dataset versions:** added content-addressed CSV storage, SHA-256 metadata, retrieval
  time, source/broker, date range, timeframe, row count and data-quality report support.
- **Prediction audit records mutable:** added ORM protection allowing only later outcome fields to be
  appended. Forecast/model/data fields cannot be rewritten.
- **No production migrations:** added Alembic V1 baseline and validation-schema revisions. Automatic
  schema creation is now configurable and intended only for local/test use.
- **No empirical evaluation harness:** added naive baselines, strict 60/20/20 chronological periods,
  three requested models, validation-only probability calibration, untouched-test metrics,
  confusion matrices, calibration buckets, permutation importance and no-trade threshold tables.
- **No real historical dataset available:** no running/installed MT5 environment or repository CSV
  exists on this machine. No download from an untrusted site was substituted. Consequently no edge
  conclusion or performance number can honestly be produced.
- **Frontend supply-chain advisories:** Next 15.5.23 transitively included vulnerable PostCSS and
  Sharp versions. The compatible remediation requires Next 16; upgrade/build validation is tracked
  in the dependency section of the final report.

## MEDIUM

- Indicator and strategy code is compact and several thresholds are domain heuristics, not fitted
  evidence. Agents may be correlated (EMA/ADX/structure/momentum overlap) and must be evaluated
  independently before any ensemble inclusion.
- VWAP is cumulative across the entire frame rather than session-reset; it is not included in the
  validated model feature list.
- The event-based backtester enters on the next open and uses stop-first ambiguity handling, but it
  does not model swaps, latency, partial fills, tick sequencing or venue-specific pip values.
- Forward-paper orchestration has a candle-boundary scheduler and outcome appender, but requires a
  continuously running worker and legitimate data feed before becoming active evidence.
- The initial dashboard uses broad TypeScript `any` types and previously displayed mojibake.
- Session/regime/timeframe slicing and agent trade-cost experiments require real data; the research
  harness cannot manufacture these reports from mock candles.

## LOW

- Source formatting is terse, and API handlers are synchronous.
- SQLite is useful locally but PostgreSQL is the intended shared environment.
- Database indexes can be expanded after real workload profiling.

## Leakage disposition

All validated model features are trailing/current-candle calculations: returns, RSI, MACD histogram,
ATR percentage, ADX, EMA spread, Bollinger position, ROC and volume z-score. Target columns are
created only after features and are explicitly excluded from `MODEL_FEATURES`. Multi-timeframe data
is not fabricated; its placeholder agent remains neutral until causally aligned higher-timeframe
features are implemented and tested.

## Safety disposition

There is no `order_send`, broker execution service, or live-trading endpoint. MT5 is read-only.
No credentials were added. Development must remain research and paper-only.
