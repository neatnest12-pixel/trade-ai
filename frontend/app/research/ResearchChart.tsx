'use client';
import { useEffect, useRef, useState } from 'react';
import { AutoscaleInfo, CandlestickSeries, ColorType, createChart, Logical, UTCTimestamp } from 'lightweight-charts';
import { Candle, Setup, decisionIndex, geometry } from './model';

export default function ResearchChart({ candles, setup, digits = 5 }: { candles: Candle[]; setup: Setup | null; digits?: number }) {
  const host = useRef<HTMLDivElement>(null), overlay = useRef<HTMLCanvasElement>(null);
  const [ohlc, setOhlc] = useState<Candle | null>(null);
  useEffect(() => {
    if (!host.current || !candles.length) return;
    const container = host.current;
    const chart = createChart(container, { height: 480, layout: { background: { type: ColorType.Solid, color: '#101821' }, textColor: '#98a9b9' },
      grid: { vertLines: { color: '#192631' }, horzLines: { color: '#192631' } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#30404d' }, rightPriceScale: { borderColor: '#30404d' },
      crosshair: { mode: 0 } });
    const series = chart.addSeries(CandlestickSeries, { upColor: '#4ccbb1', downColor: '#f18485', wickUpColor: '#4ccbb1', wickDownColor: '#f18485', borderVisible: false,
      autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => {
        const range = original();
        const plan = setup?.plan && geometry(setup.direction, setup.plan.entry, setup.plan.stop, setup.plan.target);
        if (range?.priceRange && plan) return { ...range, priceRange: {
          minValue: Math.min(range.priceRange.minValue, plan.stop, plan.target),
          maxValue: Math.max(range.priceRange.maxValue, plan.stop, plan.target),
        } };
        return range;
      },
      priceFormat: { type: 'price', precision: digits, minMove: Math.pow(10, -digits) } });
    series.setData(candles.map(c => ({ ...c, time: c.time as UTCTimestamp })));
    setOhlc(candles[candles.length - 1]);
    chart.timeScale().fitContent();
    const draw = () => {
      const canvas = overlay.current;
      if (!canvas) return;
      const width = container.clientWidth, height = 480, dpr = window.devicePixelRatio || 1;
      if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
        canvas.width = width * dpr; canvas.height = height * dpr;
      }
      const ctx = canvas.getContext('2d'); if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, width, height);
      if (!setup) return;
      const plotWidth = width - chart.priceScale('right').width(), plotHeight = height - chart.timeScale().height();
      ctx.save(); ctx.beginPath(); ctx.rect(0, 0, plotWidth, plotHeight); ctx.clip(); ctx.font = '11px system-ui';
      const logical = decisionIndex(candles, Date.parse(setup.decision_time) / 1000);
      const x = logical === null ? null : chart.timeScale().logicalToCoordinate(logical as Logical);
      if (x !== null) {
        ctx.fillStyle = '#6a8cb50c'; ctx.fillRect(x, 0, plotWidth - x, plotHeight);
        ctx.strokeStyle = '#e8bd70'; ctx.setLineDash([5, 5]); ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, plotHeight); ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = '#e8bd70'; ctx.fillText('DECISION TIME', Math.max(4, Math.min(x + 8, plotWidth - 110)), 20);
      }
      for (const zone of setup.zones) {
        const y = series.priceToCoordinate(zone.lower); if (y === null) continue;
        ctx.strokeStyle = zone.kind === 'SUPPORT' ? '#438b79' : '#9e696d'; ctx.setLineDash(zone.broken ? [2, 5] : [6, 4]);
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(plotWidth, y); ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = zone.kind === 'SUPPORT' ? '#77ba9f' : '#d5999a';
        ctx.fillText(`${zone.kind} ${zone.lower.toFixed(digits)}`, 8, y - 4);
      }
      const p = setup.plan && geometry(setup.direction, setup.plan.entry, setup.plan.stop, setup.plan.target);
      if (p && x !== null) {
        const ey = series.priceToCoordinate(p.entry), sy = series.priceToCoordinate(p.stop), ty = series.priceToCoordinate(p.target);
        if (ey !== null && sy !== null && ty !== null) {
          ctx.fillStyle = '#e8bd70'; ctx.fillText(`${setup.direction === 'SELL' ? '↓' : '↑'} ${setup.direction}`, x + 8, ey + 18);
          ctx.fillStyle = '#ed727333'; ctx.fillRect(x, Math.min(ey, sy), plotWidth - x, Math.abs(ey - sy));
          ctx.fillStyle = '#40c6a733'; ctx.fillRect(x, Math.min(ey, ty), plotWidth - x, Math.abs(ey - ty));
          for (const [label, y, color, price] of [['ENTRY', ey, '#e4edf4', p.entry], ['STOP LOSS', sy, '#f18485', p.stop], ['TAKE PROFIT', ty, '#4ccbb1', p.target]] as const) {
            ctx.strokeStyle = color; ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(plotWidth, y); ctx.stroke(); ctx.fillStyle = color; ctx.fillText(`${label} ${price.toFixed(digits)}`, Math.max(4, x + 8), y - 6);
          }
        }
      }
      ctx.restore();
    };
    chart.subscribeCrosshairMove(p => { const c = p.seriesData.get(series); if (c && 'open' in c) setOhlc({ ...c, time: Number(c.time) }); draw(); });
    chart.timeScale().subscribeVisibleLogicalRangeChange(draw);
    const observer = new ResizeObserver(() => { chart.applyOptions({ width: container.clientWidth }); draw(); }); observer.observe(container);
    // Price-scale drags have no public change subscription; redraw while the
    // pointer moves and after wheel/zoom. No idle animation loop is needed.
    container.addEventListener('pointermove', draw); container.addEventListener('wheel', draw);
    const timer = window.setInterval(draw, 250); draw();
    return () => { window.clearInterval(timer); observer.disconnect(); container.removeEventListener('pointermove', draw); container.removeEventListener('wheel', draw); chart.remove(); };
  }, [candles, setup, digits]);
  return <div>
    <div className="rv-ohlc">{ohlc ? <><span>O {ohlc.open.toFixed(digits)}</span><span>H {ohlc.high.toFixed(digits)}</span><span>L {ohlc.low.toFixed(digits)}</span><span>C {ohlc.close.toFixed(digits)}</span><span>UTC</span></> : 'OHLC UNAVAILABLE'}</div>
    <div className="rv-chart-wrap"><div ref={host} aria-label="Historical candlestick chart" />{!candles.length && <div className="rv-empty">REAL CANDLE DATA UNAVAILABLE</div>}<canvas ref={overlay} aria-hidden="true" /></div>
    <div className="rv-chart-note">Before decision: source history · After decision: subsequent market action · Scroll to zoom, drag to pan</div>
    {setup && decisionIndex(candles, Date.parse(setup.decision_time) / 1000) === null && <p className="rv-warning">Decision time is outside the available candle range. No marker position is fabricated.</p>}
  </div>;
}
