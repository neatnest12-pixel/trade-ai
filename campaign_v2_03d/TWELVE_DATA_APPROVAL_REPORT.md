# V2 #3D.12B — Twelve Data approval gate

Reviewed 2026-08-15 using official Twelve Data Terms (updated Jan 1, 2026), forex documentation, usage guidance, attribution guidance, and credits documentation.

## Technical assessment

- EUR/USD is supported; API symbol format is `EUR/USD`.
- `1min` is a documented time-series interval. The public documentation does not establish a guaranteed 2020–present or 2022–present EUR/USD M1 depth; this requires account/API verification.
- Expected fields are OHLC and provider volume where supplied. The standard time-series endpoint is not documented here as a native bid/ask or historical-spread feed.
- Timezone and historical retention limits are subscription/documentation dependent and must be confirmed before acquisition.
- API usage is credit-based; each symbol/endpoint consumes credits and plan-dependent rate/concurrency limits apply.

## Rights matrix

| Use | Status | Basis |
|---|---|---|
| Local storage | PERMITTED_WITH_PLAN | Terms license processing/storage for Internal Use or tier/add-on terms. |
| Transformation | PERMITTED_WITH_PLAN | Processing and Derived Data are allowed when non-reversible. |
| Private backtesting | PERMITTED_WITH_PLAN | Fits Internal/Non-Display use, subject to tier and third-party feed terms. |
| Private quant research | PERMITTED_WITH_PLAN | Internal use definition supports this; plan/data add-on must cover the feed. |
| Private ML training | UNCLEAR | Terms do not expressly state ML training; written confirmation required. |
| Derived features | PERMITTED_WITH_PLAN | Derived Data allowed only if raw data cannot be reverse-engineered. |
| Derived models | UNCLEAR | Model weights/parameters and financial-product restrictions need confirmation. |
| Derived signals | UNCLEAR | Private signals are not expressly addressed for FX. |
| Private trading use | PERMITTED_WITH_PLAN | Internal/non-display interpretation only; confirm subscription and feed rights. |
| Retention | UNCLEAR | Terms defer to documentation for cache/retention limits. |
| Raw redistribution | RESTRICTED | Requires redistribution add-on or written agreement. |
| Commercial software | PERMITTED_WITH_PLAN | Business/internal use may be available, but commercial FX/data licensing and external display require confirmation. |

## Decision

`PRIVATE_ML_GATE = MANUAL_CONFIRMATION_REQUIRED`  
`RETENTION_GATE = UNCLEAR`  
`USAGE_GATE = MANUAL_CONFIRMATION_REQUIRED`  
`PROVIDER_DECISION = MANUAL_CONFIRMATION_REQUIRED`

No API key was present, no sample was downloaded, and no subscription was purchased. Twelve Data is not yet canonical.

Official references: [Terms](https://twelvedata.com/terms), [Forex API](https://twelvedata.com/forex), [commercial/personal usage](https://support.twelvedata.com/en/articles/5332349-commercial-and-personal-usage), [credits](https://support.twelvedata.com/en/articles/5615854-credits), [attribution](https://support.twelvedata.com/en/articles/12647398-attribution-guidelines-for-using-twelve-data).
