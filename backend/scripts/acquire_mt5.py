"""Read-only MT5 history acquisition. This module contains no execution API calls."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

from app.market_data.adapters import TIMEFRAME_MINUTES, validate_candles
from app.market_data.datasets import DatasetStore, data_quality

TERMINAL = r"C:\Program Files\MetaTrader 5\terminal64.exe"
TIMEFRAMES = {"1m": mt5.TIMEFRAME_M1, "5m": mt5.TIMEFRAME_M5, "15m": mt5.TIMEFRAME_M15,
              "1h": mt5.TIMEFRAME_H1, "4h": mt5.TIMEFRAME_H4}


def maximum_rates(symbol: str, timeframe: int, chunk: int = 50_000):
    """Page backward until the terminal's history capacity is exhausted."""
    pages=[]
    for position in range(1, 1_000_001, chunk):
        page=mt5.copy_rates_from_pos(symbol,timeframe,position,chunk)
        if page is None or not len(page): break
        pages.append(page)
        if len(page)<chunk: break
    if not pages: return None
    import numpy as np
    return np.concatenate(pages)


def require_read_only() -> None:
    if os.getenv("MT5_READ_ONLY", "").lower() != "true":
        raise RuntimeError("MT5_READ_ONLY must be explicitly true")
    if os.getenv("TRADING_EXECUTION_ENABLED", "").lower() != "false":
        raise RuntimeError("TRADING_EXECUTION_ENABLED must be explicitly false")


def discover_eurusd() -> str:
    matches=[]
    for symbol in mt5.symbols_get() or []:
        normalized=symbol.name.upper().replace("/","").replace(".","").replace("-","")
        if "EURUSD" in normalized:
            matches.append(symbol)
    visible=[symbol for symbol in matches if symbol.visible]
    candidates=visible or matches
    if not candidates:
        raise RuntimeError("Broker exposes no EUR/USD symbol")
    candidates.sort(key=lambda s:(s.name!="EURUSD",len(s.name),s.name))
    return candidates[0].name


def acquire(output: Path) -> dict[str, object]:
    require_read_only()
    if not mt5.initialize(path=TERMINAL):
        raise RuntimeError(f"MT5 initialization failed: {mt5.last_error()}")
    try:
        terminal=mt5.terminal_info();account=mt5.account_info();version=mt5.version()
        if not terminal or not terminal.connected or not account:
            raise RuntimeError("MT5 terminal is not connected")
        symbol=discover_eurusd();spec=mt5.symbol_info(symbol)
        tick=mt5.symbol_info_tick(symbol)
        if not tick: raise RuntimeError("Cannot determine broker timestamp offset")
        server_offset_seconds=round((tick.time-time.time())/3600)*3600
        if abs(server_offset_seconds)>14*3600: raise RuntimeError("Implausible broker timestamp offset")
        store=DatasetStore(output/"datasets")
        manifest={"schema_version":1,"frozen_at":datetime.now(UTC).isoformat(),"read_only":True,
          "source":"MetaTrader5","broker":account.company,"server":account.server,
          "account_mode":"DEMO" if account.trade_mode==mt5.ACCOUNT_TRADE_MODE_DEMO else "LIVE",
          "terminal":{"major":version[0],"build":version[1],"release":version[2]},
          "server_timestamp_offset_hours":server_offset_seconds/3600,
          "symbol":symbol,"symbol_specification":{"description":spec.description,"digits":spec.digits,
          "point":spec.point,"contract_size":spec.trade_contract_size,"volume_min":spec.volume_min,
          "volume_max":spec.volume_max,"volume_step":spec.volume_step},"datasets":{}}
        now=datetime.now(UTC)
        for label,constant in TIMEFRAMES.items():
            # Index 0 is the currently forming candle. Request a deliberately large count from
            # index 1; MT5 returns up to the terminal's configured/available history capacity.
            rates=maximum_rates(symbol,constant)
            if rates is None or not len(rates):
                manifest["datasets"][label]={"status":"UNAVAILABLE","error":str(mt5.last_error())};continue
            frame=pd.DataFrame(rates).rename(columns={"time":"timestamp"})
            frame["timestamp"]=pd.to_datetime(frame.timestamp-server_offset_seconds,unit="s",utc=True)
            close_delta=pd.Timedelta(minutes=TIMEFRAME_MINUTES[label])
            frame=frame[frame.timestamp+close_delta<=pd.Timestamp(now)]
            frame["source"]="mt5";frame["symbol"]=symbol;frame["timeframe"]=label
            frame=frame.rename(columns={"volume":"real_volume"})
            frame=validate_candles(frame).drop_duplicates("timestamp",keep=False)
            quality=data_quality(frame,label)
            metadata=store.persist(frame,symbol="EURUSD",timeframe=label,source="MetaTrader5",
                                   broker=f"{account.company}/{account.server}")
            manifest["datasets"][label]={**asdict(metadata),"original_broker_symbol":symbol,
              "retrieval_start":"maximum available from terminal index 1","retrieval_end":now.isoformat(),
              "quality":quality,"certified":bool(quality["accepted"])}
        output.mkdir(parents=True,exist_ok=True)
        manifest_path=output/"DATASET_MANIFEST.json"
        if manifest_path.exists():
            raise FileExistsError("DATASET_MANIFEST.json already exists; archive it before a new acquisition")
        manifest_path.write_text(json.dumps(manifest,indent=2),encoding="utf-8")
        return manifest
    finally:
        mt5.shutdown()


if __name__=="__main__":
    result=acquire(Path("certified_data"))
    print(json.dumps(result,indent=2))
