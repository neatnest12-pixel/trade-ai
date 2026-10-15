# MT5 M1 V2 quality

The bounded date-range acquisition returned 70,540 completed EURUSD M1 bars from 2026-06-09 00:18 UTC through 2026-08-14 23:59 UTC. Earlier requested windows returned no rows, establishing a broker/server history boundary rather than a pagination-only issue. The dataset is ordered and timestamp-deduplicated; currently forming bars were excluded. No candles were fabricated or synthesized. OHLC, non-positive prices, NaN/Inf, spread and gap audits remain limited by the short available interval.

Gate: PASS_WITH_LIMITATIONS.
