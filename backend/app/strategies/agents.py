from abc import ABC, abstractmethod

import pandas as pd

from app.core.volatility import DEFAULT_MAX_ATR_PCT, DEFAULT_MIN_ATR_PCT
from app.domain.schemas import AgentResult, Direction


def result(name:str, value:float, reasons:list[str], features:list[str], threshold:float=.15)->AgentResult:
    direction=Direction.BUY if value>threshold else Direction.SELL if value<-threshold else Direction.NEUTRAL
    score=min(100,max(0,50+value*50)); confidence=min(100,abs(value)*70+25)
    return AgentResult(name=name,direction=direction,score=score,confidence=confidence,reasons=reasons,
                       invalidations=["Signal invalid if its measured conditions reverse"],features_used=features)


class Agent(ABC):
    @abstractmethod
    def evaluate(self,row:pd.Series)->AgentResult: ...

class TrendAgent(Agent):
    def evaluate(self,r):
        v=(1 if r.close>r.ema_20>r.ema_50 else -1 if r.close<r.ema_20<r.ema_50 else 0)*(min(float(r.adx_14),50)/50)
        return result("Trend Agent",v,["EMA alignment weighted by ADX"],["ema_20","ema_50","adx_14"])
class MomentumAgent(Agent):
    def evaluate(self,r):
        v=((float(r.rsi_14)-50)/50 + (1 if r.macd_hist>0 else -1))/2
        return result("Momentum Agent",v,["RSI and MACD momentum composite"],["rsi_14","macd_hist"])
class PriceActionAgent(Agent):
    def evaluate(self,r):
        v=float(r.bullish_engulfing or r.hammer or r.bullish_displacement)-float(r.bearish_engulfing or r.shooting_star or r.bearish_displacement)
        return result("Price Action Agent",v,["Configurable candle-pattern evidence only"],["engulfing","hammer","displacement"])
class StructureAgent(Agent):
    def evaluate(self,r):
        v=1 if r.structure=="bullish" or r.bos_bullish else -1 if r.structure=="bearish" or r.bos_bearish else 0
        return result("Structure Agent",v,[f"Structure classified {r.structure}"],["structure","bos"])
class LiquidityAgent(Agent):
    def evaluate(self,r):
        v=float(r.sweep_low)-float(r.sweep_high)
        return result("Liquidity Agent",v,["Algorithmic liquidity-sweep interpretation"],["sweep_high","sweep_low"])
class MeanReversionAgent(Agent):
    def evaluate(self,r):
        v=max(-1,min(1,(.5-float(r.bb_position))*2)) if r.adx_14<22 else 0
        return result("Mean Reversion Agent",v,["Bollinger location in non-trending conditions"],["bb_position","adx_14"])
class BreakoutAgent(Agent):
    def evaluate(self,r):
        v=float(r.bos_bullish)-float(r.bos_bearish)
        return result("Breakout Agent",v,["Confirmed close beyond prior swing"],["bos_bullish","bos_bearish"])
class VolatilityAgent(Agent):
    def evaluate(self, r, *, min_atr_pct: float = DEFAULT_MIN_ATR_PCT,
                 max_atr_pct: float = DEFAULT_MAX_ATR_PCT):
        v=0 if min_atr_pct <= r.atr_pct <= max_atr_pct else -.2
        return result("Volatility Agent",v,["ATR percentage risk filter"],["atr_pct"])
class MultiTimeframeAgent(Agent):
    def evaluate(self,r):
        return result("Multi-Timeframe Agent",float(r.get("htf_score",0)),["Higher-timeframe context feature; does not override"],["htf_score"])

AGENTS=[TrendAgent(),MomentumAgent(),PriceActionAgent(),StructureAgent(),LiquidityAgent(),MeanReversionAgent(),BreakoutAgent(),VolatilityAgent(),MultiTimeframeAgent()]
