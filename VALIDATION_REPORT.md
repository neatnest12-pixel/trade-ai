# Navora Quant AI Validation Report

Generated: 2026-08-13

## Decision

**B — evidence is inconclusive.** No legitimate real historical EURUSD dataset or active MT5 history
was available in the environment. Mock candles were excluded from evidence. Therefore this report
does not contain fabricated test performance and does not support live trading.

## Dataset

| Field | Result |
|---|---|
| Source / broker | Unavailable; no MT5 terminal/package and no configured CSV |
| Hash | Not applicable |
| Date range | Not applicable |
| Sample count | 0 legitimate observations |
| Timeframes | 1m, 5m, 15m, 1h, 4h required; none ingested |
| Quality report | Awaiting legitimate input |

The new ingestion path creates immutable content-addressed versions with SHA-256 hashes and rejects
duplicate or incomplete-candle datasets. It does not scrape arbitrary websites.

## Evaluation status

| Required output | Status |
|---|---|
| Train / validation / untouched test periods | Not established without real data |
| Naive baselines | Implemented; no legitimate result |
| Logistic / Random Forest / Gradient Boosting | Implemented; no legitimate result |
| Agent results | Not measured on real data |
| Ensemble result | Disabled; heuristic averaging is not accepted as defensible evidence |
| Calibration | Validation-only Platt/isotonic machinery implemented; no result |
| NO TRADE thresholds | 55–75% analysis implemented; no result |
| Expectancy after costs | Unavailable |
| Maximum drawdown | Unavailable |
| Monte Carlo | Implemented but requires historical trade outcomes |
| ₹2,500 simulations at 0.25%, 0.5%, 1% | Awaiting legitimate outcomes; no scenario exceeds 1% |

## Transaction-cost and fill method

The backtester enters no earlier than the next candle open, adds spread and slippage, applies
commission, allows one position at a time, and resolves same-bar stop/target ambiguity conservatively
in favor of the stop. It supports ATR stops/targets and a maximum holding period. This remains a bar
approximation; swaps, latency, partial fills, tick sequence and broker-specific conversion need venue
validation.

## Leakage validation

Centered swing leakage and next-bar order-block leakage were fixed. Confirmed swings are visible only
after the right-hand confirmation window closes. Order blocks are visible on the displacement
confirmation bar. Automated prefix-invariance and target-alignment tests cover causal behavior. The
model feature allowlist excludes targets and unvalidated multi-timeframe placeholders.

## Forward paper status

**Inactive / insufficient evidence.** Candle-boundary scheduling and append-only outcome evaluation
are implemented, but no continuous worker or legitimate feed is configured. Historical and forward
paper results are kept separate in the validation dashboard.

## Security and dependencies

The original three high-severity npm findings were transitive PostCSS/Sharp issues through Next.js.
Next was upgraded from 15.5.23 to 16.3.0 and Sharp to 0.35.0. `npm audit` now reports **0 known
vulnerabilities**. The production frontend build and TypeScript check pass. No credentials or order
execution functions exist.

## Tests

The expanded deterministic suite covers integrity, feature prefix invariance, delayed swing
confirmation, order-block causality, target alignment, chronological splits, dataset hashing,
quality rejection, calibration buckets, no-trade thresholds, Monte Carlo determinism, journal
immutability, scheduling, risk, fills, API behavior and core indicators. Exact final counts appear in
the completion handoff.

## Known limitations and next evidence step

Connect the read-only MT5 adapter to an authorized terminal or provide a broker-exported versioned
EURUSD CSV. Run quality gates for every timeframe, freeze train/validation choices, evaluate agents
and models, select calibration/thresholds using validation only, then run the untouched test once.
Remain in paper mode regardless of the first result.
