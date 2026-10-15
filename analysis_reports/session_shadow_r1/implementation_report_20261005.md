# Session hypothesis shadow implementation report

STRATEGY SHADOW VERSION: v3.5-eurusd-session-shadow-r1

FILES ADDED:
- backend/app/v3_5/session_shadow.py
- backend/app/v3_5/session_shadow_store.py
- backend/app/v3_5/shadow_risk.py
- backend/app/v3_5/SESSION_SHADOW.md
- backend/scripts/v35_session_shadow_replay.py
- backend/tests/v3_5/test_session_shadow.py
- analysis_reports/session_shadow_r1/broker_mechanics_20261005.json
- analysis_reports/session_shadow_r1/historical_replay_20261005.json (preliminary)
- analysis_reports/session_shadow_r1/historical_replay_certified_20261005.json (final, implementation hashes sealed)
- runtime/v35_session_shadow_r1/shadow.sqlite3 (separate new sidecar)

FILES MODIFIED: None of the pre-existing project files during this mission.

ASIAN RANGE: Implemented, requires complete M1 coverage; pre-London boundary convention documented.
LIQUIDITY SWEEP: Implemented for Asian and confirmed swing levels; exact intrabar duration unavailable.
MARKET STRUCTURE: Reused canonical confirmation-time pivots, HH/HL/LH/LL, split at data gaps.
MSS/BOS: Existing BOS/CHoCH reused; MSS explicitly aliases canonical CHoCH.
DISPLACEMENT: Quantitative ATR/body/range/impulse/volume measurements and prior-distribution classification.
FVG: Three consecutive completed candles; causal mitigation/fill measurements.
HTF CONTEXT: H1/H4/H6/D1 supported where supplied; historical H4/D1 missing, no invented zones.
CANDLE INTELLIGENCE: Existing patterns reused; geometry and configured marubozu, with location/session/liquidity context.
LONDON SWEEP/REVERSAL: Executable candidate/component detector, tested positive and missing-data cases.
NY CONTINUATION: Executable breakout/retest/component detector, tested positive case.
LONDON CLOSE MEAN REVERSION: Executable extension/reference/exhaustion detector, tested positive and unknown cases.
SESSION/DST: Reuses timezone-aware engine; DST mismatch and weekends tested. Calendar authority GENERIC_TEMPLATE.
SPREAD: Prior exclusive-session distributions; replay NORMAL 302, ELEVATED 1, EXTREME 3, UNKNOWN 80.
VOLATILITY: Prior exclusive-session distributions; replay LOW 93, NORMAL 122, HIGH 58, EXTREME 33, UNKNOWN 80.
MACRO/NEWS: Point-in-time schema only; no trusted source connected; default unavailable/UNKNOWN, no news block.

BROKER RISK MECHANICS: Read-only authenticated ICMarketsSC-Demo symbol audit at 2026-10-05T09:23:59Z. Contract 100000, tick size 0.00001, loss tick value USD 1, lot min .01/max 200/step .01, stops level 0. Standalone tested calculator rounds down and respects risk/minimum/stop constraints. Fees/slippage/swaps/currency changes/margin sufficiency are not certified; execution disconnected.

SHADOW STORE: runtime/v35_session_shadow_r1/shadow.sqlite3
APPEND-ONLY: PASS; UPDATE/DELETE blocked in disposable fixtures only. Production triggers present.
IDEMPOTENCY: PASS; repeated real replay sample ALREADY_RECORDED.
DUPLICATES: 0
STORE QUICK_CHECK: ok
STORE FOREIGN KEYS: no violations; no campaign foreign-key references.
NO-LOOKAHEAD: PASS tests; future candles, confirmation knowledge, distributions, tick availability and macro actuals rejected. Final replay verifies stable implementation hashes and unchanged campaign fingerprints.

DECISION PATH CHANGED: NO
SIGNAL FILTER ADDED: NO
RISK PATH CHANGED: NO (utility separate)
ORDERS ENABLED: NO

HISTORICAL RESULTS: DESCRIPTIVE ONLY / NOT VALIDATED

| Hypothesis | Confirmed N | Unique candidate days | BUY/SELL descriptors | Absent | UNKNOWN | Candidate WR/expectancy/PF/drawdown |
|---|---:|---:|---|---:|---:|---|
| Asia/London sweep/reversal | 0 | 0 | 0/0 | 0 | 386 | Unavailable |
| NY overlap continuation | 0 | 0 | 0/0 | 81 | 305 | Unavailable |
| London-close mean reversion | 0 | 0 | 0/0 | 0 | 386 | Unavailable |

Replay used 1544 distinct certified bar identities and 386 eligible observations. Complete Asian ranges and comparable extension distributions are absent. All 212 resolved outcomes fall in UNKNOWN candidate groups. Their original aggregate 60.37736% / +0.223032R / 1.556002 is not candidate performance. Gross sequential existing-outcome drawdown 45.44609R is computable, but not an actual overlapping-position portfolio curve. No candidate-specific trade outcomes, costs or edge were manufactured. Missing observations are not negative samples.

Warnings: small N, two decision dates, temporal clustering, repeated event observations, overlap, multiple comparisons, selection bias, incomplete costs and no independent effective sample size/confidence interval certification.

PROSPECTIVE RECORDS: 0
REPLAY RECORDS: 1
PROSPECTIVE CHECKPOINTS: 50/100/250/500 all pending. Read-only prospective_status also tracks days/sessions/regimes; counts never certify an edge.

RESOLVED BEFORE: 212
RESOLVED AFTER: 212
WINS: 128
LOSSES: 84
OUTCOME FINGERPRINT: SAME — 7e14dfc1a067931204bf2f2c4a4963ebade8247ff001ba9c719d0a4bd0fae050
OBSERVATION FINGERPRINT: SAME — d09c4c25304e77686a9f453223388339de1a16425fbba8a0841937bc598a18da (462)
SETUP FINGERPRINT: SAME — 0cc6395e10059445a409867b0ed18483671dbabee41ed38a7d6af0d0f9f66a0e (263)
V3.4 HASHES: 52/52 SAME
DECISION MANIFEST: SAME — 00ad7554d8f864768ce25720d12eafc59e7dfdc29e2b127aebbb2d2839a7557e

TESTS PASSED: 187 final full V3.5 suite, including 30 new shadow tests.
TESTS FAILED: 0

WHAT IS IMPLEMENTED: Deterministic shadow feature builder, three hypotheses, isolated versioned persistence, replay/reporting, prospective checkpoints and offline broker sizing.
WHAT IS PARTIAL: Hypothesis definitions are configurable conventions; optional components independently recorded; exact intrabar duration and executable fill inference unsupported.
WHAT IS MISSING: Trusted macro source, qualified daily range/extension reference series, historical H4/D1, formal RSI divergence/zone definitions, independently authorized collector wiring and real prospective samples.
WHAT IS NOT VALIDATED: Profitability, optimal definitions, net costs, candidate-specific trade outcomes, independent confidence intervals, cross-regime stability, execution or sizing production suitability.

SAFE FOR SHADOW OBSERVATION: YES for explicit isolated calls with certified complete point-in-time inputs. Collector integration remains pending independent recovery authorization; this report is not worker-start certification.
SAFE TO CHANGE LIVE SIGNALS: NO

COLLECTOR: Remained stopped
RESOLVER: Remained stopped
TRAINING: Not started
PAPER TRADING: Not started
REAL TRADING: Not started
ORDERS: None placed

STORAGE: Last measurement 26.172 GB free, existing guardrail passes but 30 GB worker recovery target fails. No cleanup or bulk evidence export performed. New sidecar approximately 258 KB.
NEXT ACTION: Restore >=30 GB through separately authorized storage work; independently certify unchanged worker recovery gates. After explicit collector recovery authorization, integrate the isolated shadow builder with certified completed evidence and begin prospective recording without decision changes. Do not infer an edge from these replay results.
