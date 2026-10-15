# V2 #3D.12 provider selection gate

Retrieved 2026-08-15. Requirements: EURUSD M1, minimum two complete years, preferred 3–5+, private storage/transformation/backtesting/quant research/ML/derived models.

| Provider | Technical depth/side | Storage/transform | Backtest/quant | ML/derived | Cost | Gate |
|---|---|---|---|---|---|---|
| TrueFX | Tick history, aggregate to M1; bid/ask; public download | PERMITTED for internal analysis | Reasonable internal-use reading | UNCLEAR; terms prohibit derivative works and redistribution | Free currently | MANUAL_PERMISSION_REQUIRED |
| Dukascopy | Multi-year historical bars/ticks via JForex; broker-specific feed | PERMITTED for personal non-commercial API use | REASONABLE private-use interpretation | UNCLEAR; no explicit ML grant, no distribution | API/demo access; paid account not assumed | MANUAL_PERMISSION_REQUIRED |
| HistData | Existing 2022–2026 M1, BID-only, EST/EDT-normalized | UNCLEAR | UNCLEAR | UNCLEAR | Existing files; no purchase made | MANUAL_PERMISSION_REQUIRED |

TrueFX terms explicitly allow historical data for internal viewing/analysis, grant a limited non-transferable license, and prohibit dissemination and derivative works. Therefore ML training and derived-model rights are not established: [official terms](https://www.truefx.com/truefx-terms-and-conditions/).

Dukascopy’s official API terms grant personal, non-commercial temporary access, while restricting publication/distribution and commercial storage; they do not clearly grant ML training or derived-model deployment: [API terms](https://www.dukascopy.com/swiss/docs/api/index.php). Its documentation confirms historical bars/ticks and notes demo historical data is sourced from the live environment: [history documentation](https://www.dukascopy.com/wiki/en/development/strategy-api/historical-data/overview-historical-data/).

## Disposition

HistData V2 remains immutable and quarantined from Campaign #4. MT5 V2 remains a live/current-data and forward-paper source only. No provider passes all intended-use rights.

**RECOMMENDED PROVIDER:** Dukascopy, subject to written permission confirming private ML training and derived-model use.

**SELECTION STATUS:** MANUAL_PERMISSION_REQUIRED

**PURCHASE REQUIRED:** NO automatic purchase; if Dukascopy requires a paid account/API tier, obtain user approval first.

**PERMISSION REQUIRED:** YES. Confirm local storage, transformation, private backtesting, quant research, ML training, derived-model use, retention, and redistribution restrictions in writing.
