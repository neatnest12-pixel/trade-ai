from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import BacktestRun, PaperTrade, Prediction
from app.db.session import Base, engine, get_db
from app.domain.schemas import BacktestRequest, RiskRequest
from app.market_data.adapters import CSVAdapter, MockAdapter, MT5Adapter, identify_gaps
from app.services.backtest import run_backtest
from app.services.metrics import prediction_metrics
from app.services.prediction import predict
from app.services.risk import calculate_risk

settings=get_settings()

def configured_adapter():
    if settings.market_data_source == "mt5":
        return MT5Adapter()
    if settings.market_data_source == "csv":
        if not settings.dataset_path:
            raise RuntimeError("DATASET_PATH is required for CSV market data")
        return CSVAdapter(settings.dataset_path)
    if settings.market_data_source == "mock" and settings.app_env not in {"development", "test"}:
        raise RuntimeError("Mock market data is forbidden outside development/test")
    return MockAdapter()

adapter=configured_adapter()

@asynccontextmanager
async def lifespan(app:FastAPI):
    if settings.auto_create_schema:
        Base.metadata.create_all(engine)
    yield

app=FastAPI(title=settings.app_name,version="0.1.0",lifespan=lifespan,
 description="Research, backtesting and paper-trading API. No live order execution.")
app.add_middleware(CORSMiddleware,allow_origins=settings.origins,allow_methods=["*"],allow_headers=["*"])

@app.exception_handler(ValueError)
async def value_error_handler(_,exc:ValueError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=422,content={"error":{"code":"VALIDATION_ERROR","message":str(exc)}})

@app.get("/health")
def health(): return {"status":"ok","mode":"research-only","data_source":settings.market_data_source}

@app.get("/symbols")
def symbols(): return {"symbols":[{"symbol":"EURUSD","timeframes":["1m","5m","15m","1h","4h"]}]}

@app.get("/market/{symbol}/{timeframe}")
def market(symbol:str,timeframe:str,limit:int=Query(300,ge=10,le=5000)):
    if symbol!="EURUSD": raise HTTPException(404,"Symbol not configured")
    frame=adapter.fetch(symbol,timeframe,limit)
    rows=frame.copy(); rows["timestamp"]=rows.timestamp.astype(str)
    source=settings.market_data_source
    return {"symbol":symbol,"timeframe":timeframe,"source":source,"is_live":source=="mt5",
            "gaps":identify_gaps(frame,timeframe),"candles":rows.to_dict("records")}

@app.get("/analysis/{symbol}/{timeframe}")
def analysis(symbol:str,timeframe:str):
    response,snapshot=predict(adapter.fetch(symbol,timeframe,500),symbol,timeframe)
    return {"prediction":response,"features":snapshot,"smc_notice":"Liquidity/SMC labels are algorithmic interpretations."}

@app.get("/prediction/{symbol}/{timeframe}")
def prediction(symbol:str,timeframe:str,persist:bool=False,db:Session=Depends(get_db)):
    response,snapshot=predict(adapter.fetch(symbol,timeframe,500),symbol,timeframe)
    if persist:
        exists=db.scalar(select(Prediction).where(Prediction.symbol==symbol,Prediction.timeframe==timeframe,
          Prediction.candle_timestamp==response.candle_timestamp,Prediction.model_version=="heuristic-v1"))
        if not exists:
            db.add(Prediction(created_at=datetime.now(UTC),symbol=symbol,timeframe=timeframe,
              candle_timestamp=response.candle_timestamp,probability_up=response.probability_up,
              probability_down=response.probability_down,expected_high=response.expected_high,
              expected_low=response.expected_low,expected_range=response.expected_range,
              expected_close_return=response.expected_close_return,confidence=response.confidence,
              market_regime=response.market_regime,decision=response.decision,
              strategy_outputs=[v.model_dump(mode="json") for v in response.strategy_votes],feature_snapshot=snapshot,
              model_version="heuristic-v1",data_version="mock-v1")); db.commit()
    return response

@app.get("/strategies/{symbol}/{timeframe}")
def strategies(symbol:str,timeframe:str): return prediction(symbol,timeframe).strategy_votes

@app.get("/predictions/history")
def history(limit:int=Query(100,ge=1,le=1000),db:Session=Depends(get_db)):
    return db.scalars(select(Prediction).order_by(Prediction.created_at.desc()).limit(limit)).all()

@app.get("/performance")
def performance(db:Session=Depends(get_db)):
    rows=db.scalars(select(Prediction).where(Prediction.evaluated_at.is_not(None))).all()
    return prediction_metrics([1 if r.actual_direction=="UP" else 0 for r in rows],[r.probability_up for r in rows])

@app.get("/validation/status")
def validation_status(db:Session=Depends(get_db)):
    evaluated=db.scalars(select(Prediction).where(Prediction.evaluated_at.is_not(None))).all()
    forward=[row for row in evaluated if not row.data_version.startswith("mock")]
    return {"historical_test":{"status":"UNAVAILABLE","reason":"No legitimate versioned historical dataset is configured"},
      "forward_paper_test":{"status":"INSUFFICIENT_EVIDENCE","predictions_tested":len(forward),
       "sample_size_warning":len(forward)<100},"must_not_mix":True}

@app.post("/risk/calculate")
def risk(req:RiskRequest): return calculate_risk(req)

@app.post("/backtests")
def backtests(req:BacktestRequest,db:Session=Depends(get_db)):
    run=BacktestRun(created_at=datetime.now(UTC),status="RUNNING",request=req.model_dump(),results=None); db.add(run);db.commit()
    run.results=run_backtest(adapter.fetch(req.symbol,req.timeframe,2000),req);run.status="COMPLETED";db.commit();db.refresh(run)
    return {"id":run.id,"status":run.status,"results":run.results}

@app.get("/backtests/{run_id}")
def get_backtest(run_id:int,db:Session=Depends(get_db)):
    run=db.get(BacktestRun,run_id)
    if not run: raise HTTPException(404,"Backtest not found")
    return run

@app.get("/paper-trades")
def paper_trades(db:Session=Depends(get_db)): return db.scalars(select(PaperTrade).order_by(PaperTrade.created_at.desc())).all()

@app.post("/paper-trades")
def create_paper_trade(symbol:str,timeframe:str,signal:str,entry:float,stop:float,target:float,position_size:float,db:Session=Depends(get_db)):
    if signal not in {"BUY","SELL"}: raise HTTPException(422,"Paper signal must be BUY or SELL")
    trade=PaperTrade(created_at=datetime.now(UTC),symbol=symbol,timeframe=timeframe,signal=signal,entry=entry,
      stop=stop,target=target,position_size=position_size,strategy_version="v1",status="OPEN")
    db.add(trade);db.commit();db.refresh(trade);return trade
