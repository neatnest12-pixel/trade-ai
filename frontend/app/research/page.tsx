'use client';
import { useEffect, useRef, useState } from 'react';
import ResearchChart from './ResearchChart';
import { Snapshot, Setup, positionSize } from './model';
import './research.css';

const frames = ['M1', 'M5', 'M15', 'H1', 'H6', 'H12'];
const display = (value: unknown) => value === null || value === undefined || value === '' ? 'UNAVAILABLE' : String(value);
const stamp = (value?: string | null) => value ? value.replace('T', ' ').replace('+00:00', ' UTC') : 'UNAVAILABLE';

export default function ResearchPage() {
  const [data, setData] = useState<Snapshot | null>(null), [error, setError] = useState('');
  const [timeframe, setTimeframe] = useState('M1'), [symbol, setSymbol] = useState('EURUSD');
  const [observation, setObservation] = useState(''), [refresh, setRefresh] = useState(0), [busy, setBusy] = useState(false);
  const [clock, setClock] = useState(0);
  const [balance, setBalance] = useState(''), [risk, setRisk] = useState('1'), [entry, setEntry] = useState(''), [stop, setStop] = useState('');
  const frozen = useRef(new Map<string, Setup>());
  useEffect(() => { setClock(Date.now()); const timer = setInterval(() => setClock(Date.now()), 1000); return () => clearInterval(timer); }, []);
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    async function load() {
      setBusy(true); setError('');
      try {
        const q = new URLSearchParams({ timeframe, symbol }); if (observation) q.set('observation', observation);
        const response = await fetch(`/api/research?${q}`, { cache: 'no-store', signal: controller.signal });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'SOURCE_UNAVAILABLE');
        const next = result as Snapshot;
        if (next.campaign.setup) {
          const setup = next.campaign.setup, prior = frozen.current.get(setup.id);
          if (prior && prior.snapshot_hash !== setup.snapshot_hash) throw new Error('IMMUTABLE_DECISION_SNAPSHOT_CHANGED');
          if (!prior) frozen.current.set(setup.id, setup);
          next.campaign.setup = frozen.current.get(setup.id)!;
        }
        if (!cancelled) setData(next);
      } catch (e) {
        if (!cancelled) { setError(e instanceof Error ? e.message : 'SOURCE_UNAVAILABLE'); setData(null); }
      } finally { if (!cancelled) setBusy(false); }
    }
    void load();
    return () => { cancelled = true; controller.abort(); };
  }, [timeframe, symbol, observation, refresh]);
  // Manual refresh keeps the market-closed page quiet and avoids overlapping
  // terminal reads. The displayed source acquisition time is always visible.
  const campaign = data?.campaign, market = data?.market;
  const currentAge = market?.tick_timestamp && clock ? (clock - Date.parse(market.tick_timestamp)) / 1000 : market?.tick_age;
  const currentStatus = market?.status === 'LIVE' && (currentAge == null || currentAge < 0 || currentAge > 300) ? 'STALE' : market?.status;
  const setup = campaign?.setup?.symbol === symbol ? campaign.setup : null;
  const sizing = positionSize(Number(balance), Number(risk), Number(entry), Number(stop), market?.metadata ?? null);
  const features = setup?.features.filter(f => !f.feature.startsWith('strategy_input_payload.session.')) ?? [];
  const certified = features.filter(f => f.status === 'CERTIFIED').length;
  const checks = { MT5_connected: !!market?.connected, symbol_selected: !!market?.selected, tick_fresh: currentStatus === 'LIVE',
    ...Object.fromEntries(frames.map(tf => [`${tf}_available`, !!market?.timeframes?.[tf]])), ...campaign?.readiness };

  return <main className="rv">
    <header className="rv-header"><a className="rv-brand" href="/research"><span className="rv-mark">N</span><div>NAVORA <small>QUANT RESEARCH / V3.4</small></div></a><div className="rv-header-right"><span className="rv-pill">READ ONLY</span><span>Execution disabled</span><a href="/">Legacy dashboard ↗</a></div></header>
    <div className="rv-heading"><div><div className="rv-eyebrow">STAGE 5 · PROSPECTIVE VALIDATION</div><h1>Trade visualizer<span>.</span></h1><p>Frozen decisions. Observable evidence. No execution.</p></div><button onClick={() => setRefresh(x => x + 1)} disabled={busy}>{busy ? 'Reading sources…' : 'Refresh sources ↻'}</button></div>
    {error && <div role="alert" className="rv-warning">{error} — source values are hidden until a successful read.</div>}
    <section className="rv-market" aria-label="Market status"><div><span className={`rv-status ${currentStatus === 'LIVE' ? 'live' : ''}`}>{busy ? 'CHECKING' : currentStatus ?? 'UNAVAILABLE'}</span><strong>{symbol}</strong><span>{currentStatus === 'LIVE' ? 'LIVE MARKET DATA' : market ? 'HISTORICAL DISPLAY DATA' : 'NO MARKET DATA'}</span></div><div><span>Tick <b>{stamp(market?.tick_timestamp)}</b></span><span>Tick age now <b>{market?.tick_age == null ? '—' : `${market.tick_age.toFixed(1)}s`} / 300s</b></span><span>Spread <b>{market?.spread?.toFixed(market?.metadata?.digits ?? 5) ?? '—'}</b></span></div><small>Checked {stamp(market?.checked_at)} · A connected terminal does not certify freshness. Weekend closure is inferred only for a stale Saturday FX quote.</small></section>
    <div className="rv-workspace"><section className="rv-card rv-chart-card">
      <div className="rv-toolbar"><label>Instrument <select value={symbol} onChange={e => setSymbol(e.target.value)} disabled={busy}>{['EURUSD', 'XAUUSD', 'XAGUSD', 'USDJPY'].map(s => <option key={s}>{s}</option>)}</select></label><div className="rv-frames" aria-label="Timeframe">{frames.map(tf => <button aria-pressed={timeframe === tf} className={timeframe === tf ? 'selected' : ''} key={tf} onClick={() => setTimeframe(tf)} disabled={busy}>{tf}</button>)}</div></div>
      <ResearchChart candles={market?.candles ?? []} setup={setup} digits={market?.metadata?.digits} />
      {market?.chart_source && <p className="rv-warning">Historical MT5 archive captured {stamp(market.chart_captured_at)}. This supplies chart candles only; current market and instrument metadata remain unavailable.</p>}
      <div className="rv-chart-footer"><span>Latest completed candle <b>{stamp(market?.latest_completed_candle)}</b></span><span>MT5 historical OHLC · UTC normalized</span></div>
    </section><section className="rv-card rv-setup"><div className="rv-eyebrow">NAVORA TRADE AI</div><h2>Research setup</h2><span className="rv-tag">RULE-BASED RESEARCH SETUP</span>
      <label className="rv-observation">Decision snapshot<select value={observation || campaign?.choices[0]?.id || ''} onChange={e => setObservation(e.target.value)} disabled={busy}>{campaign?.choices.map(c => <option key={c.id} value={c.id}>{c.decision.slice(0, 19).replace('T', ' ')} UTC</option>)}</select></label>
      <div className={`rv-direction ${setup?.direction}`}>{setup?.direction === 'NO_TRADE' ? 'NO TRADE' : setup?.direction ?? 'UNAVAILABLE'}</div>
      <p>{setup?.plan_reason.replaceAll('_', ' ') ?? 'No V3.4 setup for this symbol. Cross-asset display does not add training inputs.'}</p>
      <dl>{[['Symbol / timeframe', setup ? `${setup.symbol} / ${setup.timeframe}` : null], ['Entry', setup?.plan?.entry], ['Stop loss', setup?.plan?.stop], ['Take profit', setup?.plan?.target], ['SL distance', setup?.plan?.risk_distance], ['TP distance', setup?.plan?.reward_distance], ['Risk : reward', setup?.plan ? `1 : ${setup.plan.rr.toFixed(2)}` : null], ['Regime', setup?.regime], ['Session', setup?.session], ['Strategy', setup?.families.join(', ')], ['Decision time', setup?.decision_time], ['Execution', 'DISABLED']].map(([k,v]) => <div key={String(k)}><dt>{k}</dt><dd>{display(v)}</dd></div>)}</dl>
      <p>{setup?.reasons.join(' · ')}</p><div className="rv-lock">Training locked · Orders disabled</div>
    </section></div>
    <div className="rv-grid"><section className="rv-card"><div className="rv-eyebrow">IMMUTABLE CAMPAIGN</div><h2>Prospective collection</h2><p className="rv-mono">{campaign?.campaign_id ?? 'UNAVAILABLE'}</p><p>Cutoff {stamp(campaign?.cutoff)}</p><div className="rv-counts">{[['Observations', campaign?.observations], ['Eligible', campaign?.eligible], ['No trade', campaign?.no_trade], ['Resolved', campaign?.resolved], ['Pending', campaign?.pending]].map(([label,value]) => <div key={label}><strong>{value ?? '—'}</strong><span>{label}</span></div>)}</div><p>No performance or correlated RR outcomes are exposed.</p><div className="rv-buckets">{Object.entries(campaign?.bucket_plan ?? {}).map(([key, range]) => <span key={key}>Bucket {key}<b>Dates {range.join('–')}</b>{key === 'D' ? 'Untouched holdout' : `${campaign?.dates.filter(d => d[2] === key).length ?? 0} observed dates`}</span>)}</div></section>
    <section className="rv-card"><div className="rv-eyebrow">READ-ONLY ESTIMATE</div><h2>Position size calculator</h2><p>Uses {symbol} broker tick value in the account currency. This estimate excludes commission, swap and slippage.</p><div className="rv-inputs">{[['Account balance', balance, setBalance], ['Risk %', risk, setRisk], ['Entry', entry, setEntry], ['Stop loss', stop, setStop]].map(([label,value,setter]) => <label key={String(label)}>{String(label)}<input type="number" min="0" step="any" value={String(value)} onChange={e => (setter as (v: string) => void)(e.target.value)} /></label>)}</div><div className="rv-size">{sizing ? `${sizing.lots.toFixed(4)} lots` : 'POSITION SIZE UNAVAILABLE'}</div><p>{sizing ? `Risk ${sizing.riskAmount.toFixed(2)} ${sizing.currency} · SL distance ${sizing.distance.toFixed(6)}` : 'Enter valid values. Valid tick size, loss tick value, currency and volume constraints are required.'}</p><p>Instrument metadata: {market?.metadata ? `${market.metadata.account_currency} · tick ${market.metadata.tick_size} · loss tick value ${market.metadata.tick_value_loss} · contract ${market.metadata.contract_size}` : 'UNAVAILABLE'}</p></section></div>
    <div className="rv-grid"><section className="rv-card"><div className="rv-eyebrow">DECISION SNAPSHOT</div><h2>Support & resistance</h2><p>Recorded engine levels; point-in-time provenance remains uncertified. Lines have zero-width boundaries; no artificial zones are added.</p>{setup?.zones.length ? <div className="rv-zones">{setup.zones.map((z,i) => <div key={i}><span className={z.kind === 'SUPPORT' ? 'rv-green' : 'rv-red'}>{z.kind}</span><b>{z.lower.toFixed(5)} – {z.upper.toFixed(5)}</b><small>{z.broken ? 'Broken at decision' : 'Recorded level'}</small></div>)}</div> : <p>ZONE DATA UNAVAILABLE</p>}</section>
    <section className="rv-card"><div className="rv-eyebrow">INFORMATION ONLY · NO WORKER CONTROLS</div><h2>Market-reopen readiness</h2><div className="rv-checks">{Object.entries(checks).map(([key,ok]) => <div key={key}><span>{key.replaceAll('_',' ')}</span><b className={ok ? 'rv-green' : 'rv-amber'}>{ok ? 'PASS' : 'NOT READY'}</b></div>)}</div><p>Lock absence alone does not prove worker absence. The reopen command checks the protected baseline and runs the existing preflight; it never launches workers.</p></section></div>
    <section className="rv-card rv-provenance"><div className="rv-section-head"><div><div className="rv-eyebrow">DEVELOPER / RESEARCH EVIDENCE</div><h2>Point-in-time provenance</h2></div><span className="rv-pill">{certified} / {features.length || 28} MARKET INPUTS CERTIFIED</span></div><p>Exact stored evidence for the selected decision. Viewing current candles cannot repair historical provenance.</p><div className="rv-vector"><strong>TRAINING VECTOR: {setup?.vector.status ?? 'REJECTED'}</strong><span>TRAINING_ALLOWED = false</span></div><details><summary>View training-vector rejection reasons ({setup?.vector.reasons.length ?? 0})</summary><ul>{setup?.vector.reasons.map(r => <li key={r}>{r}</li>)}</ul></details><div className="rv-table"><table><thead><tr>{['Feature','Value','Source','Source timestamp','Acquired','Decision','Status / reason'].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{features.map(f => <tr key={f.feature}><td>{f.feature}</td><td>{display(f.value)}</td><td>{display(f.source)}</td><td>{stamp(f.source_timestamp)}</td><td>{stamp(f.acquisition_timestamp)}</td><td>{stamp(f.decision_timestamp)}</td><td><b className={f.status === 'CERTIFIED' ? 'rv-green' : 'rv-amber'}>{f.status}</b><small>{f.reason}</small></td></tr>)}</tbody></table></div></section>
    <footer className="rv-footer"><span>V3.4 · PRE_COMMISSION · Commission unavailable · Swap configured, not realized · Slippage unavailable</span><span>FIT_MODEL 0 / ORDER_SEND 0 / HOLDOUT BLINDED</span></footer>
  </main>;
}
