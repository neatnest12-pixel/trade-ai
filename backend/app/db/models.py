from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "timestamp", "source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    timeframe: Mapped[str] = mapped_column(String(10), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    tick_volume: Mapped[float] = mapped_column(Float, default=0)
    real_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(30))
    is_complete: Mapped[bool] = mapped_column(Boolean, default=True)


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "candle_timestamp", "model_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    timeframe: Mapped[str] = mapped_column(String(10), index=True)
    candle_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    probability_up: Mapped[float] = mapped_column(Float)
    probability_down: Mapped[float] = mapped_column(Float)
    expected_high: Mapped[float] = mapped_column(Float)
    expected_low: Mapped[float] = mapped_column(Float)
    expected_range: Mapped[float] = mapped_column(Float)
    expected_close_return: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    market_regime: Mapped[str] = mapped_column(String(40))
    decision: Mapped[str] = mapped_column(String(20))
    strategy_outputs: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    feature_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    model_version: Mapped[str] = mapped_column(String(50))
    data_version: Mapped[str] = mapped_column(String(50))
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    actual_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    direction_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    high_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    low_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    range_error: Mapped[float | None] = mapped_column(Float, nullable=True)


@event.listens_for(Prediction, "before_update")
def protect_prediction_audit_fields(_mapper, _connection, target: Prediction) -> None:
    """Only outcome fields may be appended after a prediction is inserted."""
    from sqlalchemy import inspect
    allowed = {"evaluated_at", "actual_direction", "actual_high", "actual_low", "actual_close",
               "direction_correct", "high_error", "low_error", "range_error"}
    changed = {a.key for a in inspect(target).attrs if a.history.has_changes()}
    forbidden = changed - allowed
    if forbidden:
        raise ValueError(f"Immutable prediction fields cannot be changed: {sorted(forbidden)}")


class BacktestRun(Base):
    __tablename__ = "backtests"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20))
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    results: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    symbol: Mapped[str] = mapped_column(String(20))
    timeframe: Mapped[str] = mapped_column(String(10))
    signal: Mapped[str] = mapped_column(String(10))
    entry: Mapped[float] = mapped_column(Float)
    stop: Mapped[float] = mapped_column(Float)
    target: Mapped[float] = mapped_column(Float)
    position_size: Mapped[float] = mapped_column(Float)
    spread: Mapped[float] = mapped_column(Float, default=0)
    slippage: Mapped[float] = mapped_column(Float, default=0)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    strategy_version: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="OPEN")


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    version: Mapped[str] = mapped_column(String(80), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    timeframe: Mapped[str] = mapped_column(String(10), index=True)
    source: Mapped[str] = mapped_column(String(50))
    broker: Mapped[str | None] = mapped_column(String(100), nullable=True)
    timezone: Mapped[str] = mapped_column(String(30))
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rows: Mapped[int]
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    quality_report: Mapped[dict[str, Any]] = mapped_column(JSON)


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dataset_version: Mapped[str] = mapped_column(ForeignKey("dataset_versions.version"))
    strategy_version: Mapped[str] = mapped_column(String(80))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON)
    training_dates: Mapped[dict[str, str]] = mapped_column(JSON)
    validation_dates: Mapped[dict[str, str]] = mapped_column(JSON)
    test_dates: Mapped[dict[str, str]] = mapped_column(JSON)
    results: Mapped[dict[str, Any]] = mapped_column(JSON)
