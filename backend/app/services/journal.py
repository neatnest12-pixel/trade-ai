from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Prediction


def evaluate_pending(db: Session, frame: pd.DataFrame, symbol: str, timeframe: str) -> int:
    """Append outcomes for pending predictions once the immediately following candle exists."""
    data=frame.sort_values("timestamp").reset_index(drop=True)
    lookup={pd.Timestamp(ts).to_pydatetime():i for i,ts in enumerate(data.timestamp)}
    pending=db.scalars(select(Prediction).where(Prediction.symbol==symbol,
      Prediction.timeframe==timeframe,Prediction.evaluated_at.is_(None))).all()
    count=0
    for prediction in pending:
        index=lookup.get(prediction.candle_timestamp)
        if index is None or index+1>=len(data): continue
        actual=data.iloc[index+1]; prior=data.iloc[index]
        direction="UP" if actual.close>prior.close else "DOWN"
        prediction.evaluated_at=datetime.now(UTC);prediction.actual_direction=direction
        prediction.actual_high=float(actual.high);prediction.actual_low=float(actual.low);prediction.actual_close=float(actual.close)
        predicted="UP" if prediction.probability_up>=.5 else "DOWN"
        prediction.direction_correct=predicted==direction
        prediction.high_error=abs(prediction.expected_high-float(actual.high))
        prediction.low_error=abs(prediction.expected_low-float(actual.low))
        prediction.range_error=abs(prediction.expected_range-float(actual.high-actual.low));count+=1
    db.commit()
    return count
