"""Bounded read-only bridge for the local visualizer and future reopen preflight."""
import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from app.v3.visualizer import ROOT, campaign_snapshot, market_snapshot, verify_baseline, historical_display_fallback


def safety():
    load_dotenv(ROOT / '.env', override=False)
    expected = dict(APP_ENV='paper', MT5_READ_ONLY='true', TRAINING_ALLOWED='false',
        TRADING_EXECUTION_ENABLED='false', DEMO_EXECUTION_ENABLED='false',
        REAL_EXECUTION_ENABLED='false', FIT_MODEL='0', ORDER_SEND='0')
    for key, value in expected.items():
        if os.environ.get(key, '').lower() != value:
            raise RuntimeError('UNSAFE_OR_MISSING_CONTROL:' + key)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['snapshot', 'verify', 'reopen'])
    parser.add_argument('--timeframe', choices=['M1', 'M5', 'M15', 'H1', 'H6', 'H12'], default='M1')
    parser.add_argument('--symbol', choices=['EURUSD', 'XAUUSD', 'XAGUSD', 'USDJPY'], default='EURUSD')
    parser.add_argument('--observation')
    args = parser.parse_args()
    safety()
    if args.mode in ('verify', 'reopen'):
        report = verify_baseline()
        if args.mode == 'verify':
            print(json.dumps(report)); return 0
    campaign = campaign_snapshot(args.observation)
    try:
        import MetaTrader5 as mt5
        market = market_snapshot(mt5, args.timeframe, args.symbol if args.mode == 'snapshot' else 'EURUSD')
    except ImportError:
        market = dict(status='UNAVAILABLE', candles=[], timeframes={}, error='MT5_MODULE_UNAVAILABLE')
    if args.mode == 'snapshot':
        market = historical_display_fallback(market)
        print(json.dumps(dict(campaign=campaign, market=market), allow_nan=False)); return 0
    if market['status'] != 'LIVE':
        status = 'MT5_CONNECTION_FAILED' if market.get('error') else 'WAITING_FOR_FRESH_MARKET'
        print(json.dumps(dict(status=status, market=market))); return 2
    if not all(campaign['readiness'].values()) or not all(market['timeframes'].values()):
        print(json.dumps(dict(status='PREFLIGHT_BLOCKED', readiness=campaign['readiness']))); return 2
    from scripts.v34_prospective_campaign import preflight
    if preflight() != 0:
        return 2
    print('READY_FOR_ONCE_SMOKE')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
