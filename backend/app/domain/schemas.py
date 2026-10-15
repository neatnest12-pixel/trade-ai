from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class Direction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    NEUTRAL = "NEUTRAL"


class AgentResult(BaseModel):
    name: str
    direction: Direction
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=100)
    reasons: list[str]
    invalidations: list[str]
    features_used: list[str]


class PredictionResponse(BaseModel):
    symbol: str
    timeframe: str
    candle_timestamp: datetime
    probability_up: float = Field(ge=0, le=1)
    probability_down: float = Field(ge=0, le=1)
    expected_high: float
    expected_low: float
    expected_close_return: float
    expected_range: float
    entry_reference: float
    stop_loss: float
    target: float
    risk_reward: float
    confidence: float = Field(ge=0, le=100)
    confidence_label: str
    evidence_status: str
    market_regime: str
    decision: str
    no_trade_reasons: list[str]
    strategy_votes: list[AgentResult]
    disclaimer: str = "Research prediction only; not financial advice or a live trade instruction."


class RiskRequest(BaseModel):
    account_balance: float = Field(default=2500, gt=0)
    risk_percent: float = Field(default=1, gt=0, le=100)
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    contract_size: float = Field(default=100_000, gt=0)
    quote_to_account_rate: float = Field(default=1, gt=0)

    @model_validator(mode="after")
    def stop_differs(self):
        if self.entry == self.stop:
            raise ValueError("entry and stop must differ")
        return self


class BacktestRequest(BaseModel):
    symbol: str = "EURUSD"
    timeframe: str = "5m"
    initial_balance: float = Field(default=2500, gt=0)
    spread: float = Field(default=0.0001, ge=0)
    commission: float = Field(default=0, ge=0)
    slippage: float = Field(default=0, ge=0)
    stop_atr: float = Field(default=1.5, gt=0)
    target_atr: float = Field(default=2, gt=0)
    max_holding: int = Field(default=12, gt=0)
