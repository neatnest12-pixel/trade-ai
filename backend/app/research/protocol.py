"""Campaign #4 PRE contracts; no model fitting or data acquisition occurs here."""
from dataclasses import dataclass, field
from enum import StrEnum

import pandas as pd


class Signal(StrEnum): LONG='LONG'; SHORT='SHORT'; NO_SIGNAL='NO_SIGNAL'
class Decision(StrEnum): BUY='BUY'; SELL='SELL'; NO_TRADE='NO_TRADE'
class Promotion(StrEnum): REJECTED='REJECTED'; INSUFFICIENT='INSUFFICIENT_EVIDENCE'; RESEARCH='RESEARCH_CANDIDATE'; PAPER='PAPER_TRADE_CANDIDATE'

@dataclass(frozen=True)
class FeatureSpec:
    feature_name: str; source_timeframe: str; lookback: int; availability_delay: int; warmup: int

@dataclass(frozen=True)
class StrategySignal:
    strategy_id: str; family: str; timestamp: pd.Timestamp; signal: Signal
    signal_strength: float = 0.0; reason_codes: tuple[str, ...] = (); required_warmup: int = 0
    parameters: dict = field(default_factory=dict); feature_provenance: tuple[str, ...] = ()

def next_candle_direction(close: pd.Series, minimum_move: float = 0.0) -> pd.Series:
    move = close.shift(-1).div(close).sub(1)
    return move.where(move.abs() > minimum_move, 'NO_MEANINGFUL_MOVE').where(move <= -minimum_move, 'UP')

def chronological_split(frame: pd.DataFrame, train_end: str, validation_end: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not frame.timestamp.is_monotonic_increasing: raise ValueError('chronological input required')
    t1=pd.Timestamp(train_end, tz='UTC'); t2=pd.Timestamp(validation_end, tz='UTC'); ts=pd.to_datetime(frame.timestamp,utc=True)
    return frame[ts<=t1], frame[(ts>t1)&(ts<=t2)], frame[ts>t2]

def embargo(frame: pd.DataFrame, until: pd.Timestamp) -> pd.DataFrame:
    return frame[pd.to_datetime(frame.timestamp,utc=True) > pd.Timestamp(until,tz='UTC')]

def cost_adjusted_return(gross: float, spread: float = 0.0, commission: float = 0.0, slippage: float = 0.0, multiplier: float = 1.0) -> float:
    return gross - multiplier * (spread + commission + slippage)

def promotion_gate(validation_expectancy: float, baseline_expectancy: float, folds_stable: bool, stress_positive: bool, final_access_count: int) -> Promotion:
    if final_access_count != 0: return Promotion.INSUFFICIENT
    if validation_expectancy <= 0 or validation_expectancy <= baseline_expectancy or not folds_stable or not stress_positive: return Promotion.REJECTED
    return Promotion.RESEARCH
