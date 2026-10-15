"""Read-only V3.4 presentation projection. No strategy evaluation or outcome values.

All database connections are read-only. Market history is display data and never
feeds a persisted decision, provenance record, or research contract.
"""
from __future__ import annotations
from app.mt5_connection import initialize_checked, shutdown as shutdown_mt5

import hashlib
import json
import math
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.v3.prospective import campaign_manifest, load_contract, verify_policy_files
from app.core.mt5_offset import configured_offset_seconds
from app.v3_4.mt5_time import (TIMEFRAME_SECONDS, MAX_MARKET_AGE_SECONDS,
    normalize_mt5_epoch)

ROOT = Path(__file__).resolve().parents[3]
CAMPAIGN = ROOT / 'runtime/v34_prospective_validation_r1'
BASELINE = CAMPAIGN / 'offline_visualizer/baseline.json'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def verify_baseline():
    baseline = read_json(BASELINE)
    failures = [name for name, value in baseline['files'].items()
                if not (ROOT / name).is_file() or sha(ROOT / name) != value['sha256']]
    if failures:
        raise ValueError('BASELINE_INTEGRITY_FAILURE:' + ','.join(failures))
    verify_identity()
    return {'baseline_intact': True, 'files_verified': len(baseline['files'])}


def verify_identity():
    m = campaign_manifest()
    verify_policy_files(m)
    c = load_contract()
    if (m['campaign_id'] != 'V34-PV-20260925T154159' or
        m['prospective_validation_start_utc'] != '2026-09-25T15:41:59.167748+00:00' or
        c['schema_hash'] != m['training_contract_schema_hash'] or m['training_allowed']):
        raise ValueError('CAMPAIGN_OR_CONTRACT_CHANGED')
    return m, c


def ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def plan_geometry(direction, entry, stop, target):
    if direction not in ('BUY', 'SELL') or not all(finite(x) and x > 0 for x in (entry, stop, target)):
        return None
    if not (stop < entry < target if direction == 'BUY' else target < entry < stop):
        return None
    risk, reward = abs(entry - stop), abs(target - entry)
    return dict(entry=entry, stop=stop, target=target, risk_distance=risk,
                reward_distance=reward, rr=reward / risk)


def normalize_candles(rows, timeframe, offset, now):
    """Reject malformed, duplicate, future and uncompleted bars; never clip them."""
    result = []
    seen = set()
    for r in rows:
        stamp = normalize_mt5_epoch(r['time'], offset_seconds=offset, source='VISUALIZER').timestamp
        values = {k: float(r[k]) for k in ('open', 'high', 'low', 'close')}
        if stamp + timedelta(seconds=TIMEFRAME_SECONDS[timeframe]) > now:
            raise ValueError('FUTURE_OR_INCOMPLETE_CANDLE')
        if stamp in seen:
            raise ValueError('DUPLICATE_CANDLE')
        if (not all(finite(x) and x > 0 for x in values.values()) or
            values['low'] > min(values['open'], values['close']) or
            values['high'] < max(values['open'], values['close'])):
            raise ValueError('INVALID_OHLC')
        seen.add(stamp)
        result.append(dict(time=int(stamp.timestamp()), **values))
    return sorted(result, key=lambda x: x['time'])


def market_status(tick_time, now, connected, valid_quote):
    if tick_time is None or not connected or not valid_quote:
        return 'UNAVAILABLE'
    age = (now - tick_time).total_seconds()
    if age < 0:
        return 'UNAVAILABLE'
    if age <= MAX_MARKET_AGE_SECONDS:
        return 'LIVE'
    # A stale Saturday FX quote is consistent with the weekend closure. Other
    # closures cannot be inferred from connection state alone.
    return 'MARKET CLOSED' if now.weekday() == 5 else 'STALE'


def market_snapshot(mt5, timeframe='M1', symbol='EURUSD', now=None):
    if timeframe not in TIMEFRAME_SECONDS or symbol not in ('EURUSD', 'XAUUSD', 'XAGUSD', 'USDJPY'):
        raise ValueError('UNSUPPORTED_MARKET')
    now = now or datetime.now(UTC)
    out = dict(symbol=symbol, timeframe=timeframe, checked_at=now.isoformat(), status='UNAVAILABLE',
               source='MT5_READ_ONLY', candles=[], timeframes={}, freshness_limit=MAX_MARKET_AGE_SECONDS,
               tick_age=None, tick_timestamp=None, connected=False, selected=False, metadata=None)
    try:
        offset = configured_offset_seconds()
        if not initialize_checked(mt5):
            raise ValueError('MT5_INITIALIZE_FAILED')
        terminal, account = mt5.terminal_info(), mt5.account_info()
        out['connected'] = bool(terminal and terminal.connected)
        if not account or account.server != 'ICMarketsSC-Demo' or account.currency != 'USD' or account.trade_mode != 0:
            raise ValueError('ACCOUNT_SERVER_MISMATCH')
        info, tick = mt5.symbol_info(symbol), mt5.symbol_info_tick(symbol)
        out['selected'] = bool(info and info.select)
        out['server'] = account.server
        if not info or not tick:
            raise ValueError('SYMBOL_OR_TICK_UNAVAILABLE')
        stamp = normalize_mt5_epoch(tick.time_msc / 1000 if tick.time_msc else tick.time,
                                    offset_seconds=offset, source='VISUALIZER').timestamp
        valid_quote = finite(tick.bid) and finite(tick.ask) and 0 < tick.bid <= tick.ask
        out.update(tick_timestamp=stamp.isoformat(), tick_age=(now-stamp).total_seconds(),
                   bid=tick.bid, ask=tick.ask, spread=tick.ask-tick.bid, offset_seconds=offset)
        out['status'] = market_status(stamp, now, out['connected'], valid_quote)
        if stamp > now:
            raise ValueError('FUTURE_TICK_REJECTED')
        out['metadata'] = dict(symbol=symbol, account_currency=account.currency,
            digits=info.digits, point=info.point, tick_size=info.trade_tick_size,
            tick_value_loss=info.trade_tick_value_loss, contract_size=info.trade_contract_size,
            volume_min=info.volume_min, volume_max=info.volume_max, volume_step=info.volume_step,
            source='BROKER_CURRENT_METADATA', captured_at=now.isoformat())
        out['costs'] = dict(spread='OBSERVED', commission='UNAVAILABLE', swap='CONFIGURED',
            swap_long=info.swap_long, swap_short=info.swap_short, slippage='UNAVAILABLE', basis='PRE_COMMISSION')
        for tf in TIMEFRAME_SECONDS:
            rows = mt5.copy_rates_from_pos(symbol, getattr(mt5, 'TIMEFRAME_' + tf), 1, 600 if tf == timeframe else 2)
            try:
                bars = normalize_candles(rows if rows is not None else [], tf, offset, now)
                out['timeframes'][tf] = bool(bars)
                if tf == timeframe:
                    out['candles'] = bars
                    out['latest_completed_candle'] = (datetime.fromtimestamp(bars[-1]['time'], UTC) +
                        timedelta(seconds=TIMEFRAME_SECONDS[tf])).isoformat() if bars else None
            except ValueError as exc:
                out['timeframes'][tf] = False
                out.setdefault('candle_errors', {})[tf] = str(exc)
    except Exception as exc:
        out['status'] = 'UNAVAILABLE'
        out['error'] = str(exc)
    finally:
        shutdown_mt5(mt5)
    out['display_mode'] = 'LIVE MARKET DATA' if out['status'] == 'LIVE' else 'HISTORICAL DISPLAY DATA'
    return out


def historical_display_fallback(market):
    """Previously captured real candles only; never reclassify old quotes as live."""
    if market.get('candles') or market.get('status') == 'LIVE':
        return market
    path = CAMPAIGN / 'offline_visualizer/display_snapshot.json'
    seal = path.with_suffix('.sha256')
    if not path.exists() or not seal.exists() or sha(path) != seal.read_text().strip():
        return market
    saved = read_json(path)['market']
    if saved.get('symbol') != market.get('symbol') or saved.get('timeframe') != market.get('timeframe'):
        return market
    # Snapshot was captured through normalize_candles. Recheck the archive at
    # display time, including chronology, bounds and maximum history size.
    bars = saved.get('candles', [])
    if not bars or len(bars) > 600:
        return market
    try:
        checked = normalize_candles(bars, saved['timeframe'], 0, datetime.fromisoformat(saved['checked_at']))
    except (ValueError, KeyError, TypeError):
        return market
    return dict(market, candles=checked, display_mode='HISTORICAL DISPLAY DATA',
        chart_source='HASH_VERIFIED_MT5_ARCHIVE', chart_captured_at=saved['checked_at'],
        latest_completed_candle=saved.get('latest_completed_candle'))


def path_value(row, path):
    value = row
    for part in path.split('.'):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value if value is None or isinstance(value, (str, int, float, bool)) else None


def project_zones(row, decision):
    zones = []
    for z in row.get('v3_2_confluence', {}).get('support_resistance', []):
        kind = z.get('level_type', '')
        price = z.get('level_price')
        if finite(price) and price > 0 and z.get('level_created_at', 'z') <= decision:
            side = 'SUPPORT' if 'support' in kind or 'low' in kind else 'RESISTANCE' if 'resistance' in kind or 'high' in kind else None
            if side:
                zones.append(dict(kind=side, lower=price, upper=price, created_at=z['level_created_at'],
                    broken=bool(z.get('broken')), source='PERSISTED_ENGINE_LEVEL', certification='UNCERTIFIED'))
    return zones


def project_features(row, proof, names, decision):
    result = []
    for name in names:
        p = proof.get(name, {})
        status = ('DENIED' if p.get('future_derived') or p.get('synthetic') else
                  'CERTIFIED' if p.get('point_in_time_safe') else 'UNCERTIFIED' if p else 'NOT AVAILABLE')
        reason = p.get('reason', 'MISSING_PROVENANCE')
        if status == 'CERTIFIED':
            try:
                cutoff = datetime.fromisoformat(decision)
                acquired = datetime.fromisoformat(p['ingestion_timestamp'])
                available = datetime.fromisoformat(p['availability_timestamp'])
                source = datetime.fromisoformat(p['raw_source_timestamp'])
                if any(t.tzinfo is None for t in (cutoff, acquired, available, source)):
                    raise ValueError('NAIVE_TIMESTAMP')
                if max(acquired, available, source) > cutoff or path_value(row, name) is None:
                    raise ValueError('UNAVAILABLE_AT_DECISION')
            except (KeyError, ValueError, TypeError):
                status, reason = 'UNCERTIFIED', 'MISSING_OR_INVALID_DECISION_TIME_EVIDENCE'
        result.append(dict(feature=name, value=path_value(row, name), source=p.get('source_provider'),
            source_timestamp=p.get('raw_source_timestamp'), acquisition_timestamp=p.get('ingestion_timestamp'),
            decision_timestamp=decision, status=status, reason=reason))
    return result


def campaign_snapshot(selected=None):
    m, contract = verify_identity()
    with ro(CAMPAIGN / 'campaign.sqlite3') as db:
        count = db.execute('SELECT count(*),sum(eligible) FROM members').fetchone()
        resolved = db.execute('SELECT count(distinct observation_id) FROM labels WHERE rr=1').fetchone()[0]
        # No labels/results or Bucket D records are exposed, even by explicit ID.
        choices = db.execute("SELECT id,decision,eligible FROM members WHERE bucket IN ('A','B','C') ORDER BY decision DESC LIMIT 100").fetchall()
        chosen = selected or (choices[0][0] if choices else None)
        member = db.execute("SELECT decision,eligible,provenance FROM members WHERE id=? AND bucket IN ('A','B','C')", (chosen,)).fetchone()
        buckets = list(db.execute('SELECT day,ordinal,bucket FROM dates ORDER BY ordinal'))
    setup = None
    if member:
        decision, eligible, raw_proof = member
        with ro(ROOT / 'runtime/v34_certified_outcomes.sqlite3') as db:
            index = db.execute('SELECT byte_offset,byte_length FROM observations WHERE id=?', (chosen,)).fetchone()
            payload = db.execute('SELECT payload FROM setups WHERE id=?', (chosen,)).fetchone()
        if not index or index[1] > 2_000_000:
            raise ValueError('OBSERVATION_INDEX_INVALID')
        with (ROOT / 'certified_data/v3/V3_PROSPECTIVE_DATASET.jsonl').open('rb') as f:
            f.seek(index[0]); row = json.loads(f.read(index[1]))
        if row['observation_id'] != chosen or row['decision_timestamp'] != decision:
            raise ValueError('OBSERVATION_IDENTITY_MISMATCH')
        source = json.loads(payload[0]) if payload else {}
        proof = json.loads(raw_proof)
        direction = source.get('direction') if eligible else 'NO_TRADE'
        features = project_features(row, proof, contract['feature_order'], decision)
        # The existing setup projection has no execution-price contract. Do not
        # substitute a current tick or correlated RR result for a frozen entry.
        setup = dict(id=chosen, decision_time=decision, direction=direction, symbol=row['symbol'],
            timeframe='M1', regime=source.get('regime'), session=source.get('session'), families=source.get('families', []),
            plan=None, plan_reason='ENTRY_SL_TP_NOT_PERSISTED_IN_DECISION_SNAPSHOT' if eligible else 'NO_TRADE',
            reasons=[source.get('timing_reason') or ('ELIGIBLE_RESEARCH_SETUP' if eligible else 'NO_SINGLE_ELIGIBLE_DIRECTION')],
            zones=project_zones(row, decision), features=features,
            vector=dict(status='CERTIFIED' if proof.get('_contract_validation', {}).get('accepted') and all(f['status']=='CERTIFIED' for f in features) else 'REJECTED',
                reasons=[f['feature'] + ': ' + f['reason'] for f in features if f['status'] != 'CERTIFIED']),
            snapshot_hash=hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest())
    return dict(campaign_id=m['campaign_id'], cutoff=m['prospective_validation_start_utc'],
        policy_version=m['policy_version'], policy_hash=m['policy_hash'], schema_hash=contract['schema_hash'],
        bucket_plan=m['bucket_plan'], dates=buckets, observations=count[0], eligible=count[1] or 0,
        no_trade=count[0]-(count[1] or 0), resolved=resolved, pending=(count[1] or 0)-resolved,
        training_allowed=False, execution_enabled=False, fit_model=0, order_send=0,
        choices=[dict(id=r[0], decision=r[1], eligible=bool(r[2])) for r in choices], setup=setup,
        readiness=dict(policy_valid=True, campaign_valid=True,
            collector_lock_clear=not (ROOT/'runtime/v3_prospective_collector.lock').exists(),
            resolver_lock_clear=not (ROOT/'runtime/v3_outcome_resolver.lock').exists()))
