from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from app.market_data.adapters import TIMEFRAME_MINUTES, validate_candles


@dataclass(frozen=True)
class DatasetMetadata:
    version: str
    symbol: str
    source: str
    broker: str | None
    timezone: str
    timeframe: str
    start_date: str
    end_date: str
    rows: int
    retrieved_at: str
    sha256: str


class DatasetStore:
    """Content-addressed immutable CSV dataset store."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def persist(self, frame: pd.DataFrame, *, symbol: str, timeframe: str,
                source: str, broker: str | None = None) -> DatasetMetadata:
        data = validate_candles(frame)
        canonical = data.to_csv(index=False, date_format="%Y-%m-%dT%H:%M:%S.%fZ").encode()
        digest = hashlib.sha256(canonical).hexdigest()
        version = f"{symbol}-{timeframe}-{digest[:12]}"
        folder = self.root / version
        if folder.exists():
            existing = (folder / "data.csv").read_bytes()
            if hashlib.sha256(existing).hexdigest() != digest:
                raise RuntimeError("Immutable dataset version collision")
            return DatasetMetadata(**json.loads((folder / "metadata.json").read_text()))
        folder.mkdir(parents=True, exist_ok=False)
        (folder / "data.csv").write_bytes(canonical)
        metadata = DatasetMetadata(version, symbol, source, broker, "UTC", timeframe,
            data.timestamp.iloc[0].isoformat(), data.timestamp.iloc[-1].isoformat(), len(data),
            datetime.now(UTC).isoformat(), digest)
        (folder / "metadata.json").write_text(json.dumps(asdict(metadata), indent=2))
        return metadata


def data_quality(frame: pd.DataFrame, timeframe: str) -> dict[str, object]:
    data = frame.copy(); data["timestamp"] = pd.to_datetime(data.timestamp, utc=True)
    step = pd.Timedelta(minutes=TIMEFRAME_MINUTES[timeframe]); ordered=data.sort_values("timestamp")
    deltas=ordered.timestamp.diff(); missing=int(((deltas[deltas>step]/step)-1).sum())
    returns=ordered.close.pct_change(); median=returns.median(); mad=(returns-median).abs().median()
    outliers=int(((returns-median).abs()>max(10*mad, .02)).sum())
    now=pd.Timestamp.now(tz="UTC"); incomplete=int((ordered.timestamp+step>now).sum())
    spread=ordered.get("spread",pd.Series(dtype=float)).dropna()
    report={"timeframe":timeframe,"row_count":len(data),"start_timestamp":ordered.timestamp.min().isoformat(),
      "end_timestamp":ordered.timestamp.max().isoformat(),"missing_timestamps":missing,
      "duplicate_timestamps":int(data.timestamp.duplicated().sum()),"zero_volume_candles":int((data.tick_volume==0).sum()),
      "abnormal_spreads":int((spread>spread.median()*5).sum()) if len(spread) else 0,
      "extreme_return_outliers":outliers,"weekend_contamination":int((data.timestamp.dt.dayofweek>=5).sum()),
      "timezone_consistent":str(data.timestamp.dtype)=="datetime64[us, UTC]" or "UTC" in str(data.timestamp.dtype),
      "incomplete_candles":incomplete}
    report["accepted"] = not any(report[k] for k in ("duplicate_timestamps","incomplete_candles"))
    return report
