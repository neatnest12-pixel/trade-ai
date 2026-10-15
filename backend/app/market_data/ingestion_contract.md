# Historical ingestion contract

Approved-provider adapters must emit canonical UTC M1 fields `timestamp, open, high, low, close` and may emit bid, ask, spread, tick_volume, real_volume, and source metadata. Each adapter must provide provider, instrument, price side, source timezone, retrieval method/time, and usage status. Raw artifacts are immutable; unauthorized or unclear usage status fails closed. No adapter performs downloading in this preparation module.
