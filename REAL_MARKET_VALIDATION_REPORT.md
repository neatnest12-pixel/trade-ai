# First Real-Market Validation

## Evidence classification

**C — no measurable edge found.** The one certified M1 experiment did not show meaningful balanced
directional improvement and produced strongly negative expectancy after observed spread and modeled
slippage. This is research evidence, not live-trading authorization.

## Frozen data and periods

- Source: MetaTrader 5, MetaQuotes Ltd. / MetaQuotes-Demo; symbol `EURUSD`; M1.
- Dataset: `EURUSD-1m-bdee314433d1`, 100,000 candles, SHA-256
  `bdee314433d16eaa61405ab0c80f3769af2c79af632ce1f8d91458d40f00fcbf`.
- Train: 2026-05-08 18:39 UTC – 2026-07-07 10:10 UTC, 59,970 rows.
- Calibration: 2026-07-07 11:18 – 2026-07-16 09:34 UTC, 9,977 rows.
- Validation selection: 2026-07-16 09:35 – 2026-07-27 07:53 UTC, 9,977 rows.
- Final untouched test: 2026-07-27 07:23 – 2026-08-14 04:32 UTC, 19,990 rows.

## Frozen selection and final result

Random Forest with validation-only isotonic calibration had the lowest validation Brier score
(0.24664). The 55% NO TRADE threshold had the least-negative eligible validation expectancy and was
frozen before one primary final-test evaluation.

Final accuracy was 54.26%, but balanced accuracy was only 50.46%, ROC AUC 0.5301, and Brier 0.24748.
The always-DOWN baseline achieved 54.10% accuracy because the final sample was imbalanced; random
achieved 49.89%, previous reversal 51.26%, and EMA trend 49.04%. The model's
headline accuracy therefore does not establish useful edge.

## Calibration and trading simulation

Calibration was reasonable in large low-confidence buckets but weaker at sparse high confidence.
At the frozen 55% threshold: 4,656 signals were taken, 76.71% were NO TRADE, win rate was 29.32%,
expectancy was -0.19470R, profit factor 0.3240, cumulative maximum drawdown was -907.39R, and the
largest losing streak was 27. Costs used the next candle's actual MT5 spread, 0.00002 modeled
slippage, and zero commission. Entry was next-candle open and exit was that candle's close.

## ₹2,500 simulation

Compounded ending balances were ₹258.15 at 0.25% risk, ₹26.43 at 0.50%, and ₹0.27 at 1.00%.
The bootstrap risk of losing 50% was 100% in all three scenarios for these observed outcomes. These
estimates are not guarantees; they demonstrate why the configuration must not be traded.

## Audit identifiers

- Manifest SHA-256: `f67f1ac2c829e1596ab8aaa23fa6171d824b7e48deabf87923afbf55f346c732`
- Experiment SHA-256: `0ecf898c7c30bd6eee8a8986532bcb7aec2d4fe98f3182ffc3014df4351ce6dd`
- Complete machine-readable results: `certified_data/REAL_VALIDATION_RESULTS.json`.

Forward collection is read-only, hash-chained, and paper-only. It does not place, modify, check, or
cancel orders. Historical and forward results remain separate.
