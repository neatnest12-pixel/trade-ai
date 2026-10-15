# V2 #3D.12C — OANDA historical-data provider gate

Reviewed 2026-08-15 from official OANDA developer documentation and API-license materials.

## Technical evidence

- Instrument: `EUR_USD`.
- REST v20 supports M1 candles and bid (`B`), ask (`A`) and midpoint (`M`) price components; candle volume is the number of prices created during the candle.
- Historical pricing is documented back to 2005, but account/division applicability must be confirmed before relying on that depth.
- Requests require a v20 practice/live account and personal access token. The documented candle maximum is 5,000 per request, so bounded pagination is required. Returned timestamps are UTC; daily alignment defaults to America/New_York but returned times remain UTC.
- No token was available; no sample request was made.

## Usage classification

| Use | Status |
|---|---|
| Local storage | PERMITTED_WITH_RESTRICTIONS |
| Transformation / normalization | PERMITTED_WITH_RESTRICTIONS |
| Timeframe aggregation | PERMITTED_WITH_RESTRICTIONS |
| Private backtesting | PERMITTED_WITH_RESTRICTIONS |
| Private quantitative research | PERMITTED_WITH_RESTRICTIONS |
| Private ML | UNCLEAR |
| Derived features | PERMITTED_WITH_RESTRICTIONS |
| Derived models | UNCLEAR |
| Model weights / parameters | UNCLEAR |
| Derived signals | UNCLEAR |
| Private trading research | PERMITTED_WITH_RESTRICTIONS |
| Retention | UNCLEAR |
| Raw redistribution | RESTRICTED |
| Commercial derived software | UNCLEAR / plan-dependent |

The API license defines Internal Use as research, analysis and data processing within the licensee/organization, and prohibits redistribution. It does not expressly authorize ML training, model-weight retention, or derived signal distribution. Therefore this is not sufficient to approve Campaign #4.

`PRIVATE_ML_GATE = MANUAL_CONFIRMATION_REQUIRED`  
`RETENTION_GATE = UNCLEAR`  
`USAGE_GATE = MANUAL_CONFIRMATION_REQUIRED`  
`PROVIDER_DECISION = MANUAL_CONFIRMATION_REQUIRED`  
`CANONICAL_PROVIDER = NO`

Official sources: [v20 introduction](https://developer.oanda.com/rest-live-v20/introduction/), [authentication](https://developer.oanda.com/rest-live-v20/authentication/), [instrument/candles definitions](https://developer.oanda.com/rest-live-v20/instrument-df/), [pricing endpoint](https://developer.oanda.com/rest-live-v20/pricing-ep/), [API license agreement](https://docs.oanda.com/9753050d-bf8b-44ea-a51a-3624e14664d1/api_license_agreement_oc.pdf).
