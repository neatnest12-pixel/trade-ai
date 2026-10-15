"""EURUSD hypothesis features only. No decisions, outcomes, MT5 or store writes.

Definitions are analytical conventions, frozen in version r1 before replay.
Missing observations are UNKNOWN, never evidence of candidate absence.
"""
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
import math
import statistics

from .market_context import Config, context, utc, _intervals
from .broker_context import VERSION as CONTEXT_VERSION

VERSION = 'v3.5-eurusd-session-shadow-r1'
PIP = 0.0001
DURATIONS = {'M1':60,'M5':300,'M15':900,'H1':3600,'H4':14400,'H6':21600,'H12':43200,'D1':86400}


@dataclass(frozen=True)
class Candle:
    timeframe: str
    open_time: datetime
    close_time: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: float = 0
    source_id: str = ''
    symbol: str = 'EURUSD'


@dataclass(frozen=True)
class Reference:
    timestamp: datetime
    available_at: datetime
    value: float
    kind: str
    symbol: str = 'EURUSD'


@dataclass(frozen=True)
class NewsEvent:
    event_timestamp_utc: datetime
    available_at: datetime
    currency: str
    event_name: str
    importance: str
    source: str
    forecast: float | None = None
    previous: float | None = None
    actual: float | None = None
    actual_available_at: datetime | None = None


@dataclass(frozen=True)
class ShadowConfig:
    context: Config = Config()
    swing_window: int = 2
    pre_ny_minutes: int = 60
    minimum_references: int = 20
    reference_days: int = 60
    range_percentiles: tuple = (25,75)
    displacement_percentile: float = 90
    extension_percentile: float = 90
    atr_period: int = 14
    impulse_bars: int = 3
    ema_period: int = 20
    rsi_period: int = 14
    marubozu_wick_fraction: float = .05

    def __post_init__(self):
        for n in ('swing_window','pre_ny_minutes','minimum_references','reference_days','atr_period','impulse_bars','ema_period','rsi_period'):
            if getattr(self,n)<1:raise ValueError('POSITIVE_CONFIGURATION_REQUIRED')
        if not 0<self.range_percentiles[0]<self.range_percentiles[1]<100:raise ValueError('INVALID_PERCENTILES')
        if not 0<self.displacement_percentile<100 or not 0<self.extension_percentile<100:raise ValueError('INVALID_PERCENTILES')
        if not 0<=self.marubozu_wick_fraction<=.5:raise ValueError('INVALID_WICK_FRACTION')


def checked_bars(bars,t):
    t=utc(t);result={}
    for b in bars:
        if b.symbol!='EURUSD' or b.timeframe not in DURATIONS:raise ValueError('EURUSD_TIMEFRAME_REQUIRED')
        if utc(b.close_time)>t or utc(b.available_at)>t:raise ValueError('FUTURE_OR_LATE_BAR')
        if utc(b.available_at)<utc(b.close_time):raise ValueError('BAR_AVAILABLE_BEFORE_CLOSE')
        if (utc(b.close_time)-utc(b.open_time)).total_seconds()!=DURATIONS[b.timeframe]:raise ValueError('BAR_DURATION_MISMATCH')
        if not all(math.isfinite(x) for x in (b.open,b.high,b.low,b.close,b.tick_volume)):raise ValueError('NONFINITE_OHLC')
        if not 0<b.low<=min(b.open,b.close)<=max(b.open,b.close)<=b.high or b.tick_volume<0:raise ValueError('INVALID_OHLC')
        key=(b.timeframe,utc(b.open_time))
        if key in result:raise ValueError('DUPLICATE_BAR')
        result[key]=b
    return {tf:sorted([b for (k,_),b in result.items() if k==tf],key=lambda b:utc(b.open_time)) for tf in DURATIONS}


def consecutive(bars):
    return all(utc(a.close_time)==utc(b.open_time) for a,b in zip(bars,bars[1:]))


def percentile(value,references,t,kind,cfg):
    t=utc(t);values=[]
    for r in references:
        if utc(r.timestamp)>t or utc(r.available_at)>t:raise ValueError('FUTURE_REFERENCE')
        if utc(r.available_at)<utc(r.timestamp):raise ValueError('INVALID_REFERENCE_AVAILABILITY')
        if not math.isfinite(r.value):raise ValueError('NONFINITE_REFERENCE')
        if r.symbol=='EURUSD' and r.kind==kind and t-timedelta(days=cfg.reference_days)<=utc(r.timestamp)<t:
            values.append(r.value)
    if value is None or len(values)<cfg.minimum_references:return None,len(values)
    return 100*(sum(x<value for x in values)+.5*sum(x==value for x in values))/len(values),len(values)


def session_boundaries(t,cfg):
    t=utc(t);ints=_intervals(t,cfg.context);out={}
    for name in ('ASIA','LONDON','NEW_YORK'):
        # Select today's local convention; weekend observations have no daily window.
        spec=next(s for s in cfg.context.sessions if s.name==name);zone=ZoneInfo(spec.zone)
        day=t.astimezone(zone).date();found=[(a,b) for a,b,n in ints if n==name and a.astimezone(zone).date()==day]
        if not found:out[name]=None;continue
        a,b=found[0]
        out[name]=dict(session_name=name,session_open_utc=a.isoformat(),session_close_utc=b.isoformat(),
                       minutes_since_open=(t-a).total_seconds()/60,minutes_until_close=(b-t).total_seconds()/60,
                       dst_seconds=int(a.astimezone(zone).dst().total_seconds()),zone=spec.zone,authority='GENERIC_TEMPLATE')
    if out['LONDON'] and out['NEW_YORK']:
        a=max(utc(out[n]['session_open_utc']) for n in ('LONDON','NEW_YORK'));b=min(utc(out[n]['session_close_utc']) for n in ('LONDON','NEW_YORK'))
        out['LONDON_NEW_YORK_OVERLAP']=dict(session_open_utc=a.isoformat(),session_close_utc=b.isoformat()) if a<b else None
        lc=utc(out['LONDON']['session_close_utc'])
        out['LONDON_CLOSE']=dict(session_open_utc=(lc-timedelta(minutes=cfg.context.close_window_minutes)).isoformat(),session_close_utc=lc.isoformat())
    else:out['LONDON_NEW_YORK_OVERLAP']=out['LONDON_CLOSE']=None
    ny=t.astimezone(ZoneInfo('America/New_York'));roll=datetime.combine(ny.date(),time(17),ny.tzinfo)
    out['ROLLOVER']=dict(session_open_utc=(roll-timedelta(minutes=cfg.context.rollover_minutes)).astimezone(t.tzinfo).isoformat(),session_close_utc=(roll+timedelta(minutes=cfg.context.rollover_minutes)).astimezone(t.tzinfo).isoformat()) if ny.weekday()<5 else None
    out['OFF_MARKET']={'authority':'GENERIC_TEMPLATE','broker_verified':False}
    return out


def range_features(bars,start,end,t,price,references,kind,cfg,atr_value=None):
    if start is None or end is None or end<=start:return {'state':'UNKNOWN','valid':False,'reason':'NO_SESSION_WINDOW'}
    start,end,t=map(utc,(start,end,t));selected=[b for b in bars if start<=utc(b.open_time) and utc(b.close_time)<=min(end,t)]
    full=bool(t>=end and selected and utc(selected[0].open_time)==start and utc(selected[-1].close_time)==end and consecutive(selected))
    high=max((b.high for b in selected),default=None);low=min((b.low for b in selected),default=None);mid=(high+low)/2 if high is not None else None
    width=(high-low)/PIP if high is not None else None;p,n=percentile(width if full else None,references,t,kind,cfg)
    return dict(valid=full,state='UNKNOWN' if p is None else 'TIGHT' if p<cfg.range_percentiles[0] else 'WIDE' if p>=cfg.range_percentiles[1] else 'NORMAL',
                start_utc=start.isoformat(),end_utc=end.isoformat(),available_at=max((utc(b.available_at) for b in selected),default=t).isoformat(),
                high=high,low=low,midpoint=mid,range_pips=width,range_percentile=p,reference_samples=n,
                range_vs_ATR=(high-low)/atr_value if full and atr_value and atr_value>0 else None,
                current_price_vs_high=(price-high)/PIP if high is not None and price is not None else None,
                current_price_vs_low=(price-low)/PIP if low is not None and price is not None else None,
                current_price_vs_mid=(price-mid)/PIP if mid is not None and price is not None else None,
                distance_to_high_pips=(high-price)/PIP if high is not None and price is not None else None,
                distance_to_low_pips=(price-low)/PIP if low is not None and price is not None else None,
                bars=len(selected),reason=None if full else 'INCOMPLETE_POINT_IN_TIME_COVERAGE')


def atr(bars,period):
    w=bars[-period-1:]
    if len(w)<period+1 or not consecutive(w):return None
    return statistics.mean(max(b.high-b.low,abs(b.high-a.close),abs(b.low-a.close)) for a,b in zip(w,w[1:]))


def structure(bars,cfg):
    if len(bars)<2*cfg.swing_window+1:return {'state':'UNKNOWN','events':[],'latest_swing_high':None,'latest_swing_low':None}
    # Same confirmation-time pivot and BOS/CHoCH definition used by the existing application.
    import pandas as pd
    from app.analysis.structure import add_structure
    events=[];high=low=None;last=None
    # Never let a rolling pivot bridge an unobserved interval/weekend gap.
    segments=[];segment=[]
    for b in bars:
        if segment and utc(segment[-1].close_time)!=utc(b.open_time):segments.append(segment);segment=[]
        segment.append(b)
    if segment:segments.append(segment)
    for segment in segments:
        high=low=None
        frame=add_structure(pd.DataFrame([{k:getattr(b,k) for k in ('open','high','low','close')} for b in segment]),cfg.swing_window)
        for i,row in frame.iterrows():
            b=segment[i]
            fields=('swing_high','swing_low','higher_high','higher_low','lower_high','lower_low','bos_bullish','bos_bearish','choch_bullish','choch_bearish','failed_breakout_high','failed_breakout_low')
            flags={k:bool(row[k]) for k in fields}
            if flags['swing_high']:high=float(row['confirmed_swing_high_price'])
            if flags['swing_low']:low=float(row['confirmed_swing_low_price'])
            if any(flags.values()):events.append(dict(flags=flags,timestamp_utc=b.close_time.isoformat(),available_at=max(utc(x.available_at) for x in segment[:i+1]).isoformat(),swing_high=high,swing_low=low))
        last=row
    return dict(state=str(last['structure']),events=events,latest_swing_high=high,latest_swing_low=low,
                HH=bool(last['higher_high']),HL=bool(last['higher_low']),LH=bool(last['lower_high']),LL=bool(last['lower_low']),
                BOS_UP=bool(last['bos_bullish']),BOS_DOWN=bool(last['bos_bearish']),
                MSS_BULLISH=bool(last['choch_bullish']),MSS_BEARISH=bool(last['choch_bearish']),
                CHoCH={'bullish':bool(last['choch_bullish']),'bearish':bool(last['choch_bearish'])},
                definition='REUSED_ANALYSIS_STRUCTURE_CONFIRMATION_TIME; MSS aliases formally defined CHoCH')


def candle_geometry(bars,cfg):
    if not bars:return {'state':'UNKNOWN'}
    import pandas as pd
    from app.analysis.patterns import detect_patterns
    b=bars[-1];span=b.high-b.low;body=abs(b.close-b.open);upper=b.high-max(b.open,b.close);lower=min(b.open,b.close)-b.low
    w=bars[-2:] if len(bars)<2 or utc(bars[-2].close_time)==utc(b.open_time) else bars[-1:]
    row=detect_patterns(pd.DataFrame([{k:getattr(x,k) for k in ('open','high','low','close')} for x in w])).iloc[-1]
    patterns=[k for k in ('doji','hammer','shooting_star','bullish_engulfing','bearish_engulfing','rejection') if bool(row[k])]
    if span>0 and max(upper,lower)/span<=cfg.marubozu_wick_fraction:patterns.append('marubozu')
    return dict(OHLC={k:getattr(b,k) for k in ('open','high','low','close')},range=span,body=body,upper_wick=upper,lower_wick=lower,
                body_range=body/span if span else None,upper_wick_range=upper/span if span else None,lower_wick_range=lower/span if span else None,
                bullish=b.close>b.open,bearish=b.close<b.open,patterns=patterns,timestamp_utc=b.close_time.isoformat(),available_at=b.available_at.isoformat())


def sweeps(bars,level,level_type,side,known_at):
    if level is None or known_at is None:return []
    known=utc(known_at);result=[];active=None;previous=None
    for b in bars:
        # Level must have been available before this candle began, not merely before T.
        if utc(b.open_time)<known:continue
        if previous is not None and utc(previous.close_time)!=utc(b.open_time) and active is not None:
            active['continuity']='UNKNOWN_ACROSS_DATA_GAP';result.append(active);active=None
        previous=b
        excursion=(b.high-level) if side=='HIGH' else (level-b.low)
        inside=b.close<level if side=='HIGH' else b.close>level
        if excursion>0:
            if active is None:
                active=dict(level=level,level_type=level_type,side=side,penetration_pips=excursion/PIP,maximum_excursion_beyond_level=excursion/PIP,
                            time_beyond_level=None,time_beyond_level_basis='INTRABAR_DURATION_UNAVAILABLE',
                            sweep_timestamp=b.close_time.isoformat(),available_at=b.available_at.isoformat(),first_bar_open=b.open_time.isoformat())
            active['maximum_excursion_beyond_level']=max(active['maximum_excursion_beyond_level'],excursion/PIP)
        if active is not None:
            active.update(close_back_inside=inside,reclaim_distance=((level-b.close) if side=='HIGH' else (b.close-level))/PIP if inside else 0,
                          observed_episode_span_seconds=(utc(b.close_time)-utc(active['first_bar_open'])).total_seconds(),last_observed_at=b.available_at.isoformat())
            if inside:result.append(active);active=None
    if active is not None:result.append(active)
    return result


def fvgs(bars,price):
    result=[]
    for i in range(2,len(bars)):
        a,b,c=bars[i-2:i+1]
        if not consecutive([a,b,c]):continue
        if c.low>a.high:side='BULLISH';lo,hi=a.high,c.low
        elif c.high<a.low:side='BEARISH';lo,hi=c.high,a.low
        else:continue
        later=bars[i+1:];size=hi-lo
        penetration=max([0]+[(hi-x.low) if side=='BULLISH' else (x.high-lo) for x in later])
        fill=min(1,max(0,penetration/size))
        result.append(dict(direction=side,bullish_fvg=side=='BULLISH',bearish_fvg=side=='BEARISH',fvg_low=lo,fvg_high=hi,fvg_midpoint=(lo+hi)/2,
                           fvg_size_pips=size/PIP,distance_from_price=(price-(lo+hi)/2)/PIP if price is not None else None,
                           fill_percentage=100*fill,mitigated=penetration>0,fully_filled=fill>=1,
                           timestamp_utc=c.close_time.isoformat(),available_at=max(utc(x.available_at) for x in (a,b,c)).isoformat()))
    return result


def displacement(bars,cfg):
    if not bars:return {'state':'UNKNOWN'}
    b=bars[-1];previous_atr=atr(bars[:-1],cfg.atr_period);span=b.high-b.low;body=abs(b.close-b.open)
    ratios=[]
    for i in range(cfg.atr_period+1,len(bars)-1):
        a=atr(bars[:i],cfg.atr_period)
        if a and utc(bars[i-1].close_time)==utc(bars[i].open_time):ratios.append(abs(bars[i].close-bars[i].open)/a)
    ratio=body/previous_atr if previous_atr else None
    p=100*(sum(x<ratio for x in ratios)+.5*sum(x==ratio for x in ratios))/len(ratios) if ratio is not None and len(ratios)>=cfg.minimum_references else None
    state='UNKNOWN' if p is None else ('BULLISH_DISPLACEMENT' if b.close>b.open else 'BEARISH_DISPLACEMENT' if b.close<b.open else 'NO_DISPLACEMENT') if p>=cfg.displacement_percentile else 'NO_DISPLACEMENT'
    w=bars[-cfg.impulse_bars:]
    return dict(state=state,body_ATR=ratio,range_ATR=span/previous_atr if previous_atr else None,body_range=body/span if span else None,
                close_location=(b.close-b.low)/span if span else None,body_ATR_percentile=p,reference_samples=len(ratios),
                impulse_return=b.close/w[0].open-1 if len(w)==cfg.impulse_bars and consecutive(w) else None,
                relative_tick_volume=b.tick_volume/statistics.median(x.tick_volume for x in bars[:-1]) if len(bars)>cfg.minimum_references and statistics.median(x.tick_volume for x in bars[:-1])>0 else None,
                timestamp_utc=b.close_time.isoformat(),available_at=b.available_at.isoformat())


def macro(events,t):
    t=utc(t);out=[]
    for e in events:
        if not e.source or utc(e.available_at)>t:raise ValueError('UNTRUSTED_OR_FUTURE_MACRO')
        if e.actual is not None and (e.actual_available_at is None or utc(e.actual_available_at)>t or utc(e.actual_available_at)<utc(e.event_timestamp_utc)):raise ValueError('FUTURE_MACRO_ACTUAL')
        event=asdict(e);event.update(minutes_until_event=(utc(e.event_timestamp_utc)-t).total_seconds()/60,
                                     minutes_since_event=(t-utc(e.event_timestamp_utc)).total_seconds()/60,
                                     EUR_RELEVANT=e.currency=='EUR',USD_RELEVANT=e.currency=='USD',surprise=e.actual-e.forecast if e.actual is not None and e.forecast is not None else None)
        for k in ('event_timestamp_utc','available_at','actual_available_at'):
            if event[k] is not None:event[k]=utc(event[k]).isoformat()
        out.append(event)
    return {'MACRO_DATA_AVAILABLE':bool(out),'NEWS_STATE':'AVAILABLE_CONTEXT_ONLY' if out else 'UNKNOWN','events':out,'trade_block':False}


def daily_extension(bars,start,t,refs,cfg):
    if start is None:return {'state':'UNKNOWN','extension_percentile':None}
    selected=[b for b in bars if utc(b.open_time)>=utc(start)]
    if not selected or utc(selected[0].open_time)!=utc(start) or not consecutive(selected):return {'state':'UNKNOWN','extension_percentile':None,'reason':'INCOMPLETE_SESSION_COVERAGE'}
    closes=[b.close for b in selected];b=selected[-1];move=b.close-selected[0].open;span=max(x.high for x in selected)-min(x.low for x in selected);a=atr(bars,cfg.atr_period)
    p,n=percentile(abs(move)/PIP,refs,t,'LONDON_EXTENSION_PIPS',cfg)
    ema=None
    if len(closes)>=cfg.ema_period:
        ema=statistics.mean(closes[:cfg.ema_period]);alpha=2/(cfg.ema_period+1)
        for x in closes[cfg.ema_period:]:ema=alpha*x+(1-alpha)*ema
    changes=[y-x for x,y in zip(closes,closes[1:])];rsi=None
    if len(changes)>=cfg.rsi_period:
        gains=statistics.mean(max(0,x) for x in changes[-cfg.rsi_period:]);loss=statistics.mean(max(0,-x) for x in changes[-cfg.rsi_period:])
        rsi=100-100/(1+gains/loss) if loss else 100 if gains else 50
    volume=sum(x.tick_volume for x in selected);weighted=sum(((x.high+x.low+x.close)/3)*x.tick_volume for x in selected)/volume if volume else None
    return dict(state='AVAILABLE',session_move_pips=move/PIP,extension_direction='UP' if move>0 else 'DOWN' if move<0 else 'FLAT',
                london_range_pips=span/PIP,ATR_relative_range=span/a if a else None,extension_percentile=p,reference_samples=n,
                directional_persistence=sum(x*move>0 for x in changes)/len(changes) if changes and move else None,
                pullback_depth_pips=((max(closes)-b.close) if move>=0 else (b.close-min(closes)))/PIP,
                distance_from_session_mean=(b.close-statistics.mean(closes))/PIP,EMA_distance=(b.close-ema)/PIP if ema else None,
                RSI=rsi,RSI_divergence='UNKNOWN',momentum_decay=abs(changes[-1])<abs(changes[-2]) if len(changes)>=2 else None,
                weighted_reference=weighted,weighting='TICK_VOLUME_WEIGHTED_REFERENCE',
                distance_from_weighted_reference=(b.close-weighted)/PIP if weighted is not None else None)


def build(timestamp,observation_id,bars,*,bid=None,ask=None,tick_time=None,tick_available_at=None,tick_advancing=None,references=(),spread_history=(),volatility_history=(),news=(),cfg=ShadowConfig()):
    t=utc(timestamp)
    if not observation_id:raise ValueError('OBSERVATION_ID_REQUIRED')
    bytf=checked_bars(bars,t)
    if bid is not None or ask is not None:
        if bid is None or ask is None or not all(math.isfinite(x) for x in (bid,ask)) or not 0<bid<=ask:raise ValueError('INVALID_BID_ASK')
    # Validate every supplied reference, including unused kinds; no silent future-data filtering.
    percentile(None,references,t,'VALIDATION_ONLY',cfg)
    for history in (spread_history,volatility_history):
        for r in history:
            if utc(r.timestamp)>t or utc(r.available_at)>t:raise ValueError('FUTURE_CONTEXT_REFERENCE')
    if tick_time is not None and (tick_available_at is None or utc(tick_time)>t or utc(tick_available_at)>t or utc(tick_available_at)<utc(tick_time)):raise ValueError('FUTURE_OR_INVALID_TICK')
    m1=bytf['M1'];price=(bid+ask)/2 if bid is not None and ask is not None else m1[-1].close if m1 else None
    from .market_context import Bar
    ctx=context(t,'EURUSD',config=cfg.context,bid=bid,ask=ask,quote_time=tick_time,quote_available_at=tick_available_at,
                spread_history=spread_history,volatility_history=volatility_history,
                bars=[Bar('EURUSD',b.close_time,b.available_at,b.high,b.low,b.close) for b in m1])
    boundaries=session_boundaries(t,cfg);asia=boundaries['ASIA'];london=boundaries['LONDON'];ny=boundaries['NEW_YORK']
    asia_start=utc(asia['session_open_utc']) if asia else None
    asia_end=min(utc(asia['session_close_utc']),utc(london['session_open_utc'])) if asia and london else None
    ny_end=utc(ny['session_open_utc']) if ny else None
    asia_range=range_features(m1,asia_start,asia_end,t,price,references,'ASIA_RANGE_PIPS',cfg,atr(m1,cfg.atr_period))
    pre_ny=range_features(m1,ny_end-timedelta(minutes=cfg.pre_ny_minutes) if ny_end else None,ny_end,t,price,references,'PRE_NY_RANGE_PIPS',cfg,atr(m1,cfg.atr_period))
    structures={tf:structure(values,cfg) for tf,values in bytf.items() if values}
    liquid=[]
    if asia_range['valid']:
        for side in ('HIGH','LOW'):liquid+=sweeps(m1,asia_range[side.lower()],'ASIA_'+side,side,max(asia_end,utc(asia_range['available_at'])))
    # Generic swings use levels confirmed before each tested candle; no retroactive sweep assignment.
    if 'M1' in structures:
        previous={'HIGH':None,'LOW':None};events=structures['M1']['events']
        for b in m1:
            known=[e for e in events if utc(e['available_at'])<=utc(b.open_time)]
            for side in ('HIGH','LOW'):
                level=next((e for e in reversed(known) if e['flags']['swing_'+side.lower()]),None)
                if level:liquid+=sweeps([b],level['swing_'+side.lower()],'PRIOR_SWING_'+side,side,level['available_at'])
    from .market_context import _label
    for event in liquid:
        at=utc(event['available_at']);event['session']=_label(at,_intervals(at,cfg.context),cfg.context)
    fvg={tf:fvgs(values,price) for tf,values in bytf.items() if values};disp={tf:displacement(values,cfg) for tf,values in bytf.items() if values}
    geometry=candle_geometry(m1,cfg);geometry.update(session=ctx['current_session'],location={'asia':asia_range,'pre_ny':pre_ny},structure_context=structures.get('M1',{}),liquidity_context=liquid)
    current_fresh=bool(m1 and 0<=(t-utc(m1[-1].close_time)).total_seconds()<=2*DURATIONS['M1'])
    sweep_events=[e for e in liquid if e['level_type'].startswith('ASIA_') and london and utc(e['sweep_timestamp'])>=utc(london['session_open_utc'])]
    latest_sweep=sweep_events[-1] if sweep_events else None
    london_active='LONDON' in ctx['active_sessions']
    mss={tf:{k:v for k,v in structures.get(tf,{}).items() if k in ('MSS_BULLISH','MSS_BEARISH','BOS_UP','BOS_DOWN')} for tf in ('M5','M15')}
    sweep_present=None if not current_fresh or not asia_range['valid'] else bool(london_active and latest_sweep)
    reversal=dict(candidate_present=sweep_present,sweep_direction=latest_sweep['side'] if latest_sweep else None,
                  potential_reversal_direction='DOWN' if latest_sweep and latest_sweep['side']=='HIGH' else 'UP' if latest_sweep else None,
                  components={'valid_asian_range':asia_range['valid'],'london_active':london_active,'sweep':bool(latest_sweep),
                              'reclaim':latest_sweep['close_back_inside'] if latest_sweep else None,'M5_M15_structure':mss,
                              'displacement':disp.get('M1'),'FVG':fvg.get('M1',[]),'HTF_structure':{k:v for k,v in structures.items() if k in ('H1','H4','H6','D1')},'HTF_zone':'UNKNOWN'},
                  component_timestamps={'range_available_at':asia_range.get('available_at'),'sweep_available_at':latest_sweep['available_at'] if latest_sweep else None},
                  completeness_score=sum((asia_range['valid'],london_active,bool(latest_sweep),bool(latest_sweep and latest_sweep['close_back_inside'])))/4,
                  completeness_basis='4 CORE OBSERVED COMPONENTS; optional components recorded separately')
    breakout=None;retest=None
    if pre_ny['valid']:
        post=[b for b in m1 if utc(b.open_time)>=max(ny_end,utc(pre_ny['available_at']))]
        for i,b in enumerate(post):
            side='UP' if b.close>pre_ny['high'] else 'DOWN' if b.close<pre_ny['low'] else None
            if side:
                level=pre_ny['high'] if side=='UP' else pre_ny['low'];later=post[i+1:]
                breakout=dict(direction=side,level=level,distance_pips=abs(b.close-level)/PIP,body_pips=abs(b.close-b.open)/PIP,range_pips=(b.high-b.low)/PIP,
                              close_outside=True,timestamp_utc=b.close_time.isoformat(),available_at=b.available_at.isoformat())
                retest=any(x.low<=level<=x.high for x in later);break
    continuation=dict(candidate_present=None if not current_fresh or not pre_ny['valid'] else bool(ctx['is_london_ny_overlap'] and breakout),
                      direction=breakout['direction'] if breakout else None,breakout_quality_metrics=breakout,retest_present=retest,
                      structure_confirmation=mss,FVG_created=fvg.get('M1',[]),displacement=disp.get('M1'),
                      component_completeness=sum((pre_ny['valid'],bool(ctx['is_london_ny_overlap']),bool(breakout),bool(retest)))/4)
    ext=daily_extension(m1,london['session_open_utc'] if london else None,t,references,cfg)
    # Explicit UTC-day convention, separate from broker D1; no incomplete daily range claims.
    day_start=t.replace(hour=0,minute=0,second=0,microsecond=0)
    day_bars=[b for b in m1 if utc(b.open_time)>=day_start]
    day_complete=bool(day_bars and utc(day_bars[0].open_time)==day_start and consecutive(day_bars))
    ext['daily_range_pips']=(max(b.high for b in day_bars)-min(b.low for b in day_bars))/PIP if day_complete else None
    ext['daily_range_basis']='UTC_DAY_COMPLETED_BARS_ONLY; UNKNOWN WHEN COVERAGE INCOMPLETE'
    ext['daily_range_ATR']=ext['daily_range_pips']*PIP/atr(m1,cfg.atr_period) if ext['daily_range_pips'] is not None and atr(m1,cfg.atr_period) else None
    close_window=boundaries['LONDON_CLOSE'];inclose=bool(close_window and utc(close_window['session_open_utc'])<=t<utc(close_window['session_close_utc']))
    exhaustion={'momentum_decay':ext.get('momentum_decay'),'rejection_candle':'rejection' in geometry.get('patterns',[]),'failed_swing':any(e['flags']['failed_breakout_high'] or e['flags']['failed_breakout_low'] for e in structures.get('M1',{}).get('events',[])[-1:]),'structure_failure':mss,'RSI':ext.get('RSI'),'RSI_divergence':'UNKNOWN'}
    ep=ext.get('extension_percentile')
    mean_reversion=dict(candidate_present=None if ep is None or not current_fresh else bool(inclose and ep>=cfg.extension_percentile and (exhaustion['momentum_decay'] or exhaustion['rejection_candle'] or exhaustion['failed_swing'])),
                        extension_direction=ext.get('extension_direction'),mean_reversion_direction='DOWN' if ext.get('extension_direction')=='UP' else 'UP' if ext.get('extension_direction')=='DOWN' else None,
                        extension_percentile=ep,extension=ext,exhaustion_evidence=exhaustion,london_close_window=inclose,
                        component_completeness=sum((inclose,ep is not None,ep is not None and ep>=cfg.extension_percentile,bool(exhaustion['momentum_decay'] or exhaustion['rejection_candle'])))/4)
    htf={}
    for tf in ('H1','H4','H6','D1'):
        values=bytf[tf];st=structures.get(tf,{'state':'UNKNOWN'});last=values[-1] if values else None
        htf[tf]=dict(trend=st['state'],structure=st,range_pips=(max(b.high for b in values)-min(b.low for b in values))/PIP if values else None,
                     latest_completed=last.close_time.isoformat() if last else None,freshness='FRESH' if last and (t-utc(last.close_time)).total_seconds()<=2*DURATIONS[tf] else 'UNKNOWN',
                     distance_to_swing_high_pips=(st['latest_swing_high']-price)/PIP if st.get('latest_swing_high') is not None and price is not None else None,
                     distance_to_swing_low_pips=(price-st['latest_swing_low'])/PIP if st.get('latest_swing_low') is not None and price is not None else None,zones='UNKNOWN')
    daily=bytf['D1'][-1] if bytf['D1'] else None
    htf['previous_day']={'high':daily.high if daily else None,'low':daily.low if daily else None,'source':'LATEST_COMPLETED_BROKER_D1' if daily else 'UNAVAILABLE'}
    return dict(observation_id=observation_id,timestamp_utc=t.isoformat(),context_version=CONTEXT_VERSION,strategy_shadow_version=VERSION,
                symbol='EURUSD',mode='SHADOW_ONLY',configuration=asdict(cfg),session=ctx['current_session'],session_boundaries=boundaries,
                asia=asia_range,pre_ny=pre_ny,liquidity_events=liquid,structure=structures,displacement=disp,FVG=fvg,
                HTF=htf,candle=geometry,execution_context={'bid':bid,'ask':ask,'spread_pips':ctx['current_spread']/PIP if ctx['current_spread'] is not None else None,
                  'tick_age':(t-utc(tick_time)).total_seconds() if tick_time else None,'tick_advancing':tick_advancing,'spread_state':ctx['spread_state'],'rollover_proximity':ctx['is_rollover_window']},
                volatility={k:ctx[k] for k in ('atr','short_term_realized_volatility','session_relative_volatility','volatility_percentile','volatility_state')},
                macro=macro(news,t),candidates={'ASIA_LONDON_SWEEP_REVERSAL':reversal,'NY_OVERLAP_CONTINUATION':continuation,'LONDON_CLOSE_MEAN_REVERSION':mean_reversion},
                source_evidence={'bar_count':sum(len(v) for v in bytf.values()),'bar_ids':[b.source_id for b in bars],'latest_knowledge_utc':max((utc(b.available_at) for b in bars),default=t).isoformat()},
                signal_output=None,validation='DESCRIPTIVE_ONLY_NOT_VALIDATED')
