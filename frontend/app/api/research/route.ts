import { execFile } from 'node:child_process';
import path from 'node:path';
import { promisify } from 'node:util';
import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
const run = promisify(execFile);
let pending: Promise<string> | null = null;
let pendingKey = '';

// Local desktop research view only. Fixed executable and argument allowlists;
// no shell, mutation methods, strategy invocation, or fallback mock source.
export async function GET(request: NextRequest) {
  if (!['localhost', '127.0.0.1', '[::1]'].includes(request.nextUrl.hostname))
    return NextResponse.json({ error: 'LOCAL_RESEARCH_ONLY' }, { status: 403 });
  const origin = request.headers.get('origin');
  if (origin && origin !== request.nextUrl.origin)
    return NextResponse.json({ error: 'SAME_ORIGIN_REQUIRED' }, { status: 403 });
  const timeframe = request.nextUrl.searchParams.get('timeframe') ?? 'M1';
  const symbol = request.nextUrl.searchParams.get('symbol') ?? 'EURUSD';
  const observation = request.nextUrl.searchParams.get('observation');
  if (!['M1', 'M5', 'M15', 'H1', 'H6', 'H12'].includes(timeframe) ||
      !['EURUSD', 'XAUUSD', 'XAGUSD', 'USDJPY'].includes(symbol) ||
      (observation && !/^[a-f0-9]{64}$/.test(observation)))
    return NextResponse.json({ error: 'INVALID_SELECTION' }, { status: 400 });
  const key = `${timeframe}:${symbol}:${observation ?? ''}`;
  if (pending) {
    if (pendingKey !== key) return NextResponse.json({ error: 'READ_IN_PROGRESS_RETRY' }, { status: 429 });
    try {
      return new NextResponse(await pending, { headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
    } catch {
      return NextResponse.json({ error: 'READ_ONLY_SOURCE_UNAVAILABLE_OR_INTEGRITY_CHECK_FAILED' }, { status: 503 });
    }
  }
  const root = path.resolve(process.cwd(), '..');
  const args = ['-B', '-m', 'scripts.v34_visualizer', 'snapshot', '--timeframe', timeframe, '--symbol', symbol];
  if (observation) args.push('--observation', observation);
  try {
    pendingKey = key;
    pending = run(path.join(root, 'backend', '.venv', 'Scripts', 'python.exe'), args, {
      cwd: root, timeout: 30000, maxBuffer: 2 * 1024 * 1024, windowsHide: true,
      env: { ...process.env, PYTHONPATH: path.join(root, 'backend'), PYTHONDONTWRITEBYTECODE: '1',
        APP_ENV: 'paper', MT5_READ_ONLY: 'true', TRAINING_ALLOWED: 'false', FIT_MODEL: '0', ORDER_SEND: '0',
        // Same explicit default as the certified V3.4 launcher; never inferred
        // from the age of a stale tick. Override when the broker offset changes.
        V3_MT5_SERVER_UTC_OFFSET_SECONDS: process.env.V3_MT5_SERVER_UTC_OFFSET_SECONDS ?? '10800',
        TRADING_EXECUTION_ENABLED: 'false', DEMO_EXECUTION_ENABLED: 'false', REAL_EXECUTION_ENABLED: 'false' },
    }).then(result => result.stdout);
    return new NextResponse(await pending, { headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
  } catch {
    return NextResponse.json({ error: 'READ_ONLY_SOURCE_UNAVAILABLE_OR_INTEGRITY_CHECK_FAILED' }, { status: 503 });
  } finally { pending = null; }
}
