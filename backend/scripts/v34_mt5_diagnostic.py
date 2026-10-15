"""Read-only IPC probes, each bounded in a separate Python subprocess."""
import argparse
import importlib.metadata
import json
import os
import struct
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from app.mt5_connection import DEFAULT_TERMINAL, terminal_processes, shutdown
from app.core.mt5_offset import configured_offset_seconds
from app.v3_4.mt5_time import normalize_mt5_epoch, MAX_MARKET_AGE_SECONDS


def probe(mode, path, timeout):
    result = {'mode': mode, 'tests': {}}
    tests = result['tests']
    import MetaTrader5 as mt5
    try:
        args = {} if mode == 'auto' else {'path': path}
        args['timeout'] = timeout
        initialized = mt5.initialize(**args)
        error = mt5.last_error()
        tests['initialize'] = dict(status='PASS' if initialized else 'FAIL', last_error=error, arguments=args)
        for name, fn in [('terminal_info', mt5.terminal_info), ('version', mt5.version), ('account_info', mt5.account_info)]:
            value = fn()
            # No login number, balance, credential or account-holder identity.
            detail = value if name == 'version' else {k: getattr(value,k,None) for k in
                (['connected','build','path','data_path'] if name=='terminal_info' else ['server','currency','trade_mode'])}
            valid = value is not None and (name != 'version' or bool(value[0] and value[1]))
            tests[name] = dict(status='PASS' if valid else 'FAIL', evidence=detail)
        for symbol in ('EURUSD','XAUUSD'):
            info, tick = mt5.symbol_info(symbol), mt5.symbol_info_tick(symbol)
            tests[symbol] = dict(status='PASS' if info is not None else 'FAIL')
            detail = dict(status='FAIL', code='TICK_UNAVAILABLE')
            if tick is not None:
                stamp = normalize_mt5_epoch(tick.time_msc/1000 if tick.time_msc else tick.time,
                    offset_seconds=configured_offset_seconds(), source=symbol).timestamp
                age = (datetime.now(UTC)-stamp).total_seconds()
                valid = 0 < tick.bid <= tick.ask
                detail = dict(status='PASS' if valid and 0 <= age <= MAX_MARKET_AGE_SECONDS else 'FAIL',
                    timestamp=stamp.isoformat(), age_seconds=age, bid=tick.bid, ask=tick.ask,
                    code='FRESH' if valid and 0 <= age <= MAX_MARKET_AGE_SECONDS else 'STALE_OR_INVALID_TICK')
            tests[symbol+'_tick'] = detail
    except Exception as exc:
        tests['probe_exception'] = dict(status='FAIL', exception=type(exc).__name__, detail=str(exc))
    finally:
        tests['shutdown'] = shutdown(mt5)
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--probe',choices=['auto','explicit','controlled']);p.add_argument('--output')
    a=p.parse_args();path=os.getenv('V34_MT5_TERMINAL_PATH',DEFAULT_TERMINAL)
    if a.probe:
        print(json.dumps(probe(a.probe,path,10000 if a.probe!='controlled' else 15000)));return
    report=dict(utc=datetime.now(UTC).isoformat(),python=sys.executable,python_version=sys.version,
        python_arch=struct.calcsize('P')*8,terminal_path=path,tests={})
    tests=report['tests'];tests['python']=dict(status='PASS' if report['python_arch']==64 else 'FAIL')
    try:
        report['mt5_package']=importlib.metadata.version('MetaTrader5');tests['package']=dict(status='PASS')
    except importlib.metadata.PackageNotFoundError:
        tests['package']=dict(status='FAIL')
    tests['executable']=dict(status='PASS' if Path(path).is_file() else 'FAIL')
    try:
        processes=terminal_processes();tests['processes']=dict(status='PASS',evidence=processes)
    except Exception as exc:
        processes=[];tests['processes']=dict(status='FAIL',exception=type(exc).__name__)
    # initialize may auto-launch: only compare discovery paths when exactly one
    # expected terminal is already running. Never create a second instance.
    safe=len(processes)==1 and str(processes[0].get('ExecutablePath','')).casefold()==path.casefold()
    report['probes']=[]
    for mode in ('auto','explicit','controlled'):
        if not safe or tests.get('package',{}).get('status')!='PASS':
            report['probes'].append(dict(mode=mode,status='FAIL',code='PROBE_BLOCKED_NO_UNAMBIGUOUS_RUNNING_TERMINAL'));continue
        try:
            r=subprocess.run([sys.executable,'-B','-m','scripts.v34_mt5_diagnostic','--probe',mode],
                capture_output=True,text=True,timeout=25,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            report['probes'].append(json.loads(r.stdout) if r.returncode==0 else dict(mode=mode,status='FAIL',code='PROBE_PROCESS_FAILED',stderr=r.stderr[-1500:]))
        except subprocess.TimeoutExpired:
            report['probes'].append(dict(mode=mode,status='FAIL',code='PROBE_HARD_TIMEOUT',cleanup='Child terminated; terminal left untouched'))
    if a.output:Path(a.output).write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
