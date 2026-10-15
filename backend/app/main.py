import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import require_private_api
from app.db.models import BacktestRun, PaperTrade, Prediction
from app.db.session import Base, engine, get_db
from app.domain.schemas import BacktestRequest, RiskRequest
from app.market_data.adapters import CSVAdapter, MockAdapter, MT5Adapter, identify_gaps
from app.services.backtest import run_backtest
from app.services.journal import evaluate_pending
from app.services.metrics import paper_performance
from app.services.prediction import predict
from app.services.risk import calculate_risk
from app.services.runtime_state import read_state
from app.v1_1c.contract import CAMPAIGN_ID as V11C_CAMPAIGN_ID
from app.v1_1c.contract import CONFIG_HASH as V11C_CONFIG_HASH
from app.v1_1c.contract import SNAPSHOT_ID as V11C_SNAPSHOT_ID

settings=get_settings()

def configured_adapter():
    if settings.market_data_source == "mt5":
        if not settings.mt5_read_only or settings.trading_execution_enabled:
            raise RuntimeError("MT5 market data fails closed unless read-only is true and execution is false")
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
def health():
    return {"status":"ok","mode":"PAPER_ONLY","data_source":settings.market_data_source,
            "mt5_read_only":settings.mt5_read_only,"trading_execution_enabled":settings.trading_execution_enabled}

def operational_status() -> dict:
    return {"mode":"PAPER_MODE", "live_trading":"DISABLED", "broker":settings.mt5_broker, "server":settings.mt5_server,
            "worker":read_state(Path(settings.runtime_state_path)), "active_snapshot_id":settings.v1_snapshot_id, "active_config_hash":settings.v1_config_hash}


def terminal_v1() -> dict:
    """Read-only terminal projection of the frozen V1 forward journal."""
    root = Path(__file__).resolve().parents[2]
    path = root / "certified_data" / "FORWARD_PAPER_JOURNAL.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
    snapshot = "v1-paper-r4-0d891a2356e35464"
    config = "0d891a2356e35464dbe3ba9f51c50fe240e40feff84fe4008d8b1564b55f9eac"
    predictions = [r for r in rows if r.get("type") == "prediction" and r.get("v1_snapshot_id") == snapshot and r.get("v1_config_hash") == config]
    official = {}
    for row in predictions:
        key = (row.get("v1_snapshot_id"), row.get("v1_config_hash"), row.get("symbol"), row.get("timeframe"), row.get("candle_timestamp"))
        if key not in official or row.get("prediction_time", "") < official[key].get("prediction_time", ""):
            official[key] = row
    by_hash = {r.get("record_hash"): r for r in official.values()}
    evaluations = [r for r in rows if r.get("type") == "evaluation" and r.get("prediction_hash") in by_hash]
    latest = max(official.values(), key=lambda r: r.get("candle_timestamp", ""), default={})
    directional = [r for r in official.values() if r.get("decision") in {"BUY", "SELL"}]
    correct = sum(r.get("direction_correct") is True for r in evaluations)
    incorrect = sum(r.get("direction_correct") is False for r in evaluations)
    mt5_data = {"status": "UNAVAILABLE", "bid": None, "ask": None, "spread": None, "account": {"mode": "UNKNOWN"}}
    if settings.market_data_source == "mt5":
        try:
            import MetaTrader5 as mt5
            if mt5.initialize():
                info, tick = mt5.account_info(), mt5.symbol_info_tick("EURUSD")
                if info and tick:
                    mt5_data = {"status": "CONNECTED", "bid": tick.bid, "ask": tick.ask,
                                "spread": tick.ask - tick.bid,
                                "account": {"mode": "DEMO" if info.trade_mode == 0 else "REAL",
                                             "balance": info.balance, "equity": info.equity,
                                             "margin_free": info.margin_free}}
                mt5.shutdown()
        except (ImportError, RuntimeError):
            pass
    return {"snapshot_id": snapshot, "config_hash": config, "journal_status": "AVAILABLE" if path.exists() else "WAITING FOR DATA",
            "worker": read_state(root / "runtime" / "worker_state.json"), "mt5_status": mt5_data["status"],
            "bid": mt5_data["bid"], "ask": mt5_data["ask"], "spread": mt5_data["spread"],
            "account": mt5_data["account"], "recent_signals": sorted(official.values(), key=lambda r: r.get("prediction_time", ""), reverse=True)[:5],
            "latest_completed_candle": latest.get("candle_timestamp"), "latest_signal": latest,
            "paper_metrics": {"official_unique_predictions": len(official), "buy": sum(r.get("decision") == "BUY" for r in official.values()),
              "sell": sum(r.get("decision") == "SELL" for r in official.values()), "no_trade": sum(r.get("decision") == "NO TRADE" for r in official.values()),
              "completed_trade_signals": sum(r.get("record_hash") in {e.get("prediction_hash") for e in evaluations} for r in directional),
              "unresolved": len(official) - len(evaluations), "direction_accuracy": f"{correct / (correct + incorrect) * 100:.2f}%" if correct + incorrect else None,
              "current_winning_streak": None, "current_losing_streak": None}, "risk": {"kill_switch": "ENABLED"}}


def v11c_dashboard() -> dict:
    root = Path(__file__).resolve().parents[2]
    journal_path = root / "certified_data" / "FORWARD_PAPER_JOURNAL_V1_1C.jsonl"
    state = read_state(root / "runtime" / "v1_1c_worker_state.json")
    rows = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines() if line.strip()] if journal_path.exists() else []
    predictions = [row for row in rows if row.get("type") == "prediction"]
    outcomes = [row for row in rows if row.get("type") == "outcome"]
    latest = predictions[-1] if predictions else {}
    outcomes_by_prediction = {row.get("prediction_hash"): row for row in outcomes}
    trade_fields = ("prediction_hash", "candle_timestamp", "decision", "entry_price", "stop_loss",
                    "target_price", "planned_rr", "risk_percentage", "risk_rupees",
                    "position_size_simulated", "balance_before_trade")

    def trade_view(prediction: dict) -> dict:
        outcome = outcomes_by_prediction.get(prediction.get("prediction_hash"), {})
        unevaluable = False
        if outcome.get("exit_reason") == "TIME_EXIT" and outcome.get("exit_timestamp"):
            entry_time = datetime.fromisoformat(prediction["candle_timestamp"])
            exit_time = datetime.fromisoformat(outcome["exit_timestamp"])
            unevaluable = exit_time < entry_time + timedelta(minutes=5)
        return {**{field: prediction.get(field) for field in trade_fields},
                "status": "UNEVALUABLE" if unevaluable else outcome.get("status", "OPEN"),
                "integrity_status": "UNEVALUABLE" if unevaluable else "EVALUABLE",
                "exit_timestamp": outcome.get("exit_timestamp"),
                "exit_price": outcome.get("exit_price"), "exit_reason": outcome.get("exit_reason"),
                "realized_r": outcome.get("realized_r"), "realized_pnl_rupees": outcome.get("realized_pnl_rupees"),
                "balance_after_trade": outcome.get("balance_after_trade")}

    actionable = [row for row in predictions if row.get("decision") in {"BUY", "SELL"}]
    open_trades = [trade_view(row) for row in actionable if row.get("prediction_hash") not in outcomes_by_prediction]
    closed_trades = [trade_view(row) for row in actionable if row.get("prediction_hash") in outcomes_by_prediction]
    evaluable_closed = [row for row in closed_trades if row["integrity_status"] == "EVALUABLE"]
    realized_r = [float(row["realized_r"]) for row in evaluable_closed if row.get("realized_r") is not None]
    realized_pnl = [float(row["realized_pnl_rupees"]) for row in evaluable_closed if row.get("realized_pnl_rupees") is not None]
    wins = sum(value > 0 for value in realized_r)
    losses = sum(value < 0 for value in realized_r)
    losses_r = abs(sum(value for value in realized_r if value < 0))
    drawdowns = [float(row["drawdown_rupees"]) for row in outcomes if row.get("drawdown_rupees") is not None]
    return {"campaign_id": V11C_CAMPAIGN_ID, "snapshot_id": V11C_SNAPSHOT_ID,
            "config_hash": V11C_CONFIG_HASH, "min_atr_pct": .00008, "max_atr_pct": .004,
            "status": state.get("status", "PAUSED"), "worker_id": state.get("worker_id"),
            "heartbeat": state.get("last_heartbeat"), "mt5_status": "CONNECTED" if state.get("status") == "RUNNING" else state.get("status"),
            "latest": {key: latest.get(key) for key in ("candle_timestamp", "predicted_direction", "decision", "confidence", "atr_ratio", "volatility_gate", "reason_codes", "strategy_reasons", "entry_price", "stop_loss", "target_price", "planned_rr", "risk_percentage", "risk_rupees", "position_size_simulated", "balance_before_trade")},
            "paper": {"starting_balance": 2500, "current_balance": state.get("balance", "2500"),
                      "open_trades": len(open_trades), "closed_trades": len(closed_trades),
                      "evaluable": len(evaluable_closed), "unevaluable": len(closed_trades) - len(evaluable_closed),
                      "wins": wins, "losses": losses, "breakevens": sum(value == 0 for value in realized_r),
                      "win_rate": wins / (wins + losses) if wins + losses else None,
                      "realized_r": sum(realized_r) if realized_r else None,
                      "realized_pnl_rupees": sum(realized_pnl) if realized_pnl else None,
                      "expectancy_r": sum(realized_r) / len(realized_r) if realized_r else None,
                      "profit_factor": sum(value for value in realized_r if value > 0) / losses_r if losses_r else None,
                      "max_drawdown_rupees": max(drawdowns) if drawdowns else None},
            "open_trade_details": open_trades, "closed_trade_details": closed_trades,
            "trade_history": sorted(open_trades + closed_trades, key=lambda item: item["candle_timestamp"], reverse=True),
            "exit_policy": {"take_profit": "target_price", "stop_loss": "stop_loss",
                            "time_exit": "fifth subsequent M1 close",
                            "ambiguous_intrabar": "excluded from profitability"},
            "journal_records": len(rows), "safety": {"live_trading": "DISABLED", "broker_order_execution": "DISABLED"}}

@app.get("/api/status", dependencies=[Depends(require_private_api)])
def api_status(): return operational_status()

@app.get("/dashboard/v1_1c", dependencies=[Depends(require_private_api)])
def dashboard_v11c(): return v11c_dashboard()

@app.get("/terminal/v1", dependencies=[Depends(require_private_api)])
def terminal_dashboard_v1(): return terminal_v1()

@app.get("/symbols")
def symbols(): return {"symbols":[{"symbol":"EURUSD","timeframes":["1m","5m","15m","1h","4h"]}]}

@app.get("/market/{symbol}/{timeframe}", dependencies=[Depends(require_private_api)])
def market(symbol:str,timeframe:str,limit:int=Query(300,ge=10,le=5000)):
    if symbol!="EURUSD": raise HTTPException(404,"Symbol not configured")
    if timeframe not in {"1m", "5m", "15m", "1h", "4h"}:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    frame=adapter.fetch(symbol,timeframe,limit)
    rows=frame.copy(); rows["timestamp"]=rows.timestamp.astype(str)
    source=settings.market_data_source
    return {"symbol":symbol,"timeframe":timeframe,"source":source,"is_live":source=="mt5",
            "gaps":identify_gaps(frame,timeframe),"candles":rows.to_dict("records")}

@app.get("/analysis/{symbol}/{timeframe}", dependencies=[Depends(require_private_api)])
def analysis(symbol:str,timeframe:str):
    response,snapshot=predict(adapter.fetch(symbol,timeframe,500),symbol,timeframe)
    return {"prediction":response,"features":snapshot,"smc_notice":"Liquidity/SMC labels are algorithmic interpretations."}

@app.get("/prediction/{symbol}/{timeframe}", dependencies=[Depends(require_private_api)])
def prediction(symbol:str,timeframe:str,persist:bool=False,db:Session=Depends(get_db)):
    response,snapshot=predict(adapter.fetch(symbol,timeframe,500),symbol,timeframe)
    if persist:
        if not settings.v1_snapshot_id or not settings.v1_config_hash:
            raise RuntimeError("Frozen V1 snapshot identifiers are required before persisting paper predictions")
        exists=db.scalar(select(Prediction).where(Prediction.symbol==symbol,Prediction.timeframe==timeframe,
          Prediction.candle_timestamp==response.candle_timestamp,Prediction.model_version=="v1-paper-heuristic"))
        if not exists:
            db.add(Prediction(created_at=datetime.now(UTC),symbol=symbol,timeframe=timeframe,
              candle_timestamp=response.candle_timestamp,probability_up=response.probability_up,
              probability_down=response.probability_down,expected_high=response.expected_high,
              expected_low=response.expected_low,expected_range=response.expected_range,
              expected_close_return=response.expected_close_return,confidence=response.confidence,
              market_regime=response.market_regime,decision=response.decision,
              strategy_outputs=[v.model_dump(mode="json") for v in response.strategy_votes],feature_snapshot=snapshot,
              model_version="v1-paper-heuristic",data_version=settings.market_data_source,
              v1_snapshot_id=settings.v1_snapshot_id,v1_config_hash=settings.v1_config_hash)); db.commit()
    return response

@app.get("/strategies/{symbol}/{timeframe}", dependencies=[Depends(require_private_api)])
def strategies(symbol:str,timeframe:str): return prediction(symbol,timeframe).strategy_votes

@app.get("/predictions/history", dependencies=[Depends(require_private_api)])
def history(limit:int=Query(100,ge=1,le=1000),db:Session=Depends(get_db)):
    return db.scalars(select(Prediction).order_by(Prediction.created_at.desc()).limit(limit)).all()

@app.get("/performance", dependencies=[Depends(require_private_api)])
def performance(db:Session=Depends(get_db)):
    rows=db.scalars(select(Prediction).where(Prediction.v1_snapshot_id==settings.v1_snapshot_id,
      Prediction.v1_config_hash==settings.v1_config_hash).order_by(Prediction.created_at.desc())).all()
    return paper_performance(rows)

@app.post("/paper/evaluate/{symbol}/{timeframe}", dependencies=[Depends(require_private_api)])
def evaluate_paper(symbol: str, timeframe: str, db: Session = Depends(get_db)):
    """Append outcomes only; original signal fields are protected by the ORM audit guard."""
    return {"evaluated": evaluate_pending(db, adapter.fetch(symbol, timeframe, 500), symbol, timeframe)}

@app.get("/validation/status", dependencies=[Depends(require_private_api)])
def validation_status(db:Session=Depends(get_db)):
    evaluated=db.scalars(select(Prediction).where(Prediction.evaluated_at.is_not(None))).all()
    forward=[row for row in evaluated if not row.data_version.startswith("mock")]
    return {"historical_test":{"status":"UNAVAILABLE","reason":"No legitimate versioned historical dataset is configured"},
      "forward_paper_test":{"status":"INSUFFICIENT_EVIDENCE","predictions_tested":len(forward),
       "sample_size_warning":len(forward)<100},"must_not_mix":True}

@app.post("/risk/calculate", dependencies=[Depends(require_private_api)])
def risk(req:RiskRequest): return calculate_risk(req)

@app.post("/backtests", dependencies=[Depends(require_private_api)])
def backtests(req:BacktestRequest,db:Session=Depends(get_db)):
    run=BacktestRun(created_at=datetime.now(UTC),status="RUNNING",request=req.model_dump(),results=None); db.add(run);db.commit()
    run.results=run_backtest(adapter.fetch(req.symbol,req.timeframe,2000),req);run.status="COMPLETED";db.commit();db.refresh(run)
    return {"id":run.id,"status":run.status,"results":run.results}

@app.get("/backtests/{run_id}", dependencies=[Depends(require_private_api)])
def get_backtest(run_id:int,db:Session=Depends(get_db)):
    run=db.get(BacktestRun,run_id)
    if not run: raise HTTPException(404,"Backtest not found")
    return run

@app.get("/paper-trades", dependencies=[Depends(require_private_api)])
def paper_trades(db:Session=Depends(get_db)): return db.scalars(select(PaperTrade).order_by(PaperTrade.created_at.desc())).all()

@app.post("/paper-trades", dependencies=[Depends(require_private_api)])
def create_paper_trade(symbol:str,timeframe:str,signal:str,entry:float,stop:float,target:float,position_size:float,db:Session=Depends(get_db)):
    if signal not in {"BUY","SELL"}: raise HTTPException(422,"Paper signal must be BUY or SELL")
    trade=PaperTrade(created_at=datetime.now(UTC),symbol=symbol,timeframe=timeframe,signal=signal,entry=entry,
      stop=stop,target=target,position_size=position_size,strategy_version="v1",status="OPEN")
    db.add(trade);db.commit();db.refresh(trade);return trade
