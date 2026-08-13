from datetime import UTC, datetime

from app.market_data.adapters import TIMEFRAME_MINUTES, MarketDataAdapter


def next_close(timeframe: str, now: datetime | None = None) -> datetime:
    """Return the next UTC timeframe boundary for a scheduler/worker."""
    current=(now or datetime.now(UTC)).astimezone(UTC)
    minutes=TIMEFRAME_MINUTES[timeframe]; epoch=int(current.timestamp())
    seconds=minutes*60
    return datetime.fromtimestamp(((epoch//seconds)+1)*seconds,tz=UTC)


class ForwardPaperScheduler:
    """Order-free orchestration hook; a deployment worker supplies journal persistence callbacks."""
    def __init__(self, adapter: MarketDataAdapter): self.adapter=adapter
    def completed_frame(self, symbol: str, timeframe: str):
        return self.adapter.fetch(symbol,timeframe,500)
