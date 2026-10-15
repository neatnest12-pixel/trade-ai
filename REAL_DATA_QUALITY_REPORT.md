# Real MT5 Data Quality Certification

Source: MetaTrader 5, MetaQuotes Ltd. / MetaQuotes-Demo. Broker symbol: `EURUSD`. Terminal build
6111. Retrieval was read-only; index 0 (forming candle) was excluded. UTC is canonical.

| Timeframe | UTC range | Rows | Unexpected missing | Duplicates | Invalid OHLC | Incomplete | SHA-256 | Gate |
|---|---|---:|---:|---:|---:|---:|---|---|
| M1 | 2026-05-08 17:50 – 2026-08-14 04:33 | 100,000 | 4 | 0 | 0 | 0 | `bdee314433d16eaa61405ab0c80f3769af2c79af632ce1f8d91458d40f00fcbf` | PASS |
| M5 | 2025-04-10 14:05 – 2026-08-14 04:25 | 100,000 | 924 | 0 | 0 | 0 | `f58db8ab3489fe0bab1bb3bfbccf80d0ae63a65cca87b3f567682455c3380b8e` | FAIL / quarantined |
| M15 | 2022-08-04 14:00 – 2026-08-14 04:15 | 100,000 | 632 | 0 | 0 | 0 | `0b68e9831407f7d55f78eaf50e6252cc82d53a14ceb1a0a4e000ff873a21533e` | FAIL / quarantined |
| H1 | 2010-07-05 17:00 – 2026-08-14 03:00 | 99,999 | 866 | 0 | 0 | 0 | `a83bae2618b35d4e003beccf7f5e6578a97ad0f304f00aeb4dde01bf896b07db` | FAIL / quarantined |
| H4 | 1971-01-03 21:00 – 2026-08-13 21:00 | 50,164 | 54,483 | 0 | 0 | 0 | `d353b74a8c4ddd11151667d108f23f0ac0ca91f009ec35f162132e8bc9a09f23` | FAIL / quarantined |

Normal Friday-to-Sunday closures are counted separately and are not missing-data corruption. The
fixed integrity gate permits at most 0.5% unexpected missing intervals and rejects duplicates,
invalid OHLC, or incomplete bars. Only M1 passed and was admitted to empirical validation. Spread
outliers are retained and identified because they are genuine broker observations; actual spread
was used in cost simulation.

The MetaQuotes-Demo feed encoded timestamps at server UTC+3. Acquisition detected the offset from
the broker tick clock and normalized every stored candle to UTC. The initial unnormalized attempt and
forward dry-run were quarantined and excluded from evidence.
