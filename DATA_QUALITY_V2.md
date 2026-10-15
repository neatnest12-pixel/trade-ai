# Data Quality V2

Reassessment of the immutable MetaTrader 5 EURUSD datasets confirms that M1 is the only certified primary timeframe. M5, M15, H1 and H4 contain no duplicates, invalid OHLC records, or weekend contamination, but exceed the configured unexpected-gap threshold and remain uncertified. No prices were interpolated or fabricated.

The complete immutable manifest and hashes are in `certified_data/DATASET_MANIFEST.json`.
