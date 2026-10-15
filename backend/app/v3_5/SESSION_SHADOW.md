# EURUSD session hypotheses — r1 shadow specification

Version: v3.5-eurusd-session-shadow-r1. These definitions are analytical conventions,
chosen before replay and not optimized against the 212 outcome labels. They are
not certified profitable strategies. `signal_output` is always null. There is no
import or call to an order API, live decision engine or campaign writer.

## Inputs and knowledge time

`build(T, observation_id, completed_candles, ...)` accepts EURUSD only. Every candle
must have aware open/close/acquisition timestamps, finite valid OHLC and the stated
timeframe duration. Close and acquisition must be <=T. Duplicate bar identities,
future/late acquisitions, future reference distributions and future macro actuals
are errors. No future-input filtering inside the builder masks leakage.

A caller may select previously known versions before passing them to the builder.
Replay streams immutable provenance in insertion order, deduplicates unchanged
completed-bar versions, and selects the last known version available by T. It does
not query today's MT5 history to reconstruct yesterday's knowledge. Labels are
loaded only after all detections have been calculated. Features never accept
outcomes as input.

Session boundaries reuse `market_context.Config` and its ZoneInfo intervals. Their
authority remains GENERIC_TEMPLATE, not broker-certified trading hours. Exact
UTC boundaries, local zone, DST seconds and context version are recorded. Weekends
have no fabricated daily session windows. London close is the existing configurable
close-window convention; rollover uses the existing New York 17:00 local convention.

## Frozen candidate definitions

**Asian range:** Asia-session open until the earlier of Asia-session close or
London open. This is explicitly a *pre-London Asian range*, not the entire
overlapping Tokyo session. Continuous M1 coverage must start and end exactly at
these boundaries. A partial high/low may be shown descriptively, but valid=false
and candidate presence is unknown. Historical range references require at least
20 prior samples within 60 days. Midrank percentile <25 is TIGHT, >=75 WIDE;
otherwise NORMAL. These configurable percentile conventions convey no edge.

**Liquidity sweep:** a completed candle high strictly exceeds a previously known
high level, or low strictly penetrates a previously known low level. Close strictly
back inside is a separate reclaim flag. Consecutive penetrating candles form an
observed episode; missing intervals terminate continuity. Penetration, maximum
observed excursion, reclaim distance and knowledge timestamp are recorded. OHLC
cannot establish exact intrabar time beyond a level: that value stays null.
Generic swings are tested only after their confirmation was available before the
tested candle began. A sweep does not prove reversal or institutional activity.

**Structure:** reuse `app.analysis.structure.add_structure`, default window=2.
The middle candle of a 2w+1 rolling window is a high/low pivot when its extreme
equals the window maximum/minimum; ties follow the existing implementation.
It becomes observable on the rightmost confirmation candle. Acquisition-time
availability is conservatively the maximum knowledge time of the prefix. Gaps
split segments, so pivots cannot bridge unobserved intervals. HH/HL/LH/LL and
close-crossing BOS reuse that module. MSS is explicitly an alias of its existing
formally defined bullish/bearish CHoCH, not another swing algorithm. This audit
does not validate that canonical definition as a profitable concept.

**Displacement:** body/preceding simple ATR(14), range/ATR, body/range, close
location, three-candle impulse and relative tick volume. Classification uses the
current body/ATR rank against at least 20 preceding ratios; >=90th percentile is
directional displacement, otherwise NO_DISPLACEMENT. Missing distributions are
UNKNOWN. No institutional interpretation. Breakout body/range/distance are
reported separately; displacement is an optional component, not a trade filter.

**FVG:** consecutive completed A/B/C: C.low>A.high is bullish; C.high<A.low bearish.
Third-candle close/acquisition is the earliest knowledge time. Later known candle
extremes quantify capped penetration into the gap; >0 is mitigated and 100% fully
filled under this OHLC-range definition. This does not prove executable fills,
centralized volume, or predictive power.

**ASIA_LONDON_SWEEP_REVERSAL:** valid frozen range, fresh M1 evidence, active London,
and an observed Asian-boundary sweep after London opens. Reclaim, canonical M5/M15
structure, displacement, FVG and HTF evidence remain independent components. A high
sweep describes a potential DOWN reversal; a low sweep UP. No action is emitted.
Completeness is the fraction of four descriptive core flags; optional flags remain
separate and cannot affect decisions.

**NY_OVERLAP_CONTINUATION:** pre-NY range is the configurable 60 minutes before
timezone-derived NY open, requiring complete M1 coverage. First known completed
post-range close strictly outside a boundary is a breakout. Later bar intersection
with that boundary is a retest. Candidate presence requires current London/NY
overlap, valid range, fresh M1 and breakout. Structure/FVG/displacement are optional
independent evidence. Direction describes the break, not a trade instruction.

**LONDON_CLOSE_MEAN_REVERSION:** complete London-to-T M1 coverage, fresh M1, current
London-close window, >=90th percentile absolute session extension against at least
20 supplied prior references, and observed momentum decay, rejection candle or
canonical failed-breakout evidence. Caller must supply comparable historical
extension windows. Without them the candidate is UNKNOWN. Session move/range,
UTC-day range when complete, ATR, persistence, pullback, mean/EMA distance, simple
RSI(14), failed swings and structure are separately recorded. RSI divergence remains
UNKNOWN; no subjective divergence or supply/demand zones are fabricated.

## Other context and limitations

Candle geometry and doji/hammer/shooting-star/engulfing/rejection conventions reuse
`analysis.patterns`; marubozu uses configured maximum wick/range .05. Patterns carry
session, range location, liquidity and structure context, with no predictive score.
H1/H4/H6/D1 accept completed point-in-time candles. Missing historical H4/D1 are
UNKNOWN. Previous-day high/low means latest completed broker D1, not an invented
calendar day. HTF freshness is descriptive, and the layer cannot bypass H12 recovery.

Spread/volatility reuse the existing instrument/session reference framework;
insufficient histories remain UNKNOWN. Tick volume is labeled
TICK_VOLUME_WEIGHTED_REFERENCE, never institutional VWAP. The news schema supports
scheduled forecasts and time-qualified actuals; no trusted production adapter is
connected, so default MACRO_DATA_AVAILABLE=false and NEWS_STATE=UNKNOWN. No news
trade block is implemented.

Historical replay populates spread and realized-volatility distributions from
earlier eligible observations in the same exclusive session. Current values are
added only after their own features are calculated. The preliminary replay report
without these populated distributions is superseded by the certified report.

`shadow_risk.position_size` uses reported tick size and loss tick value, not an
assumed $10/pip. It rounds lots down, respects min/max/step and stops-level constraints,
and returns zero when broker minimum would exceed risk. Outputs exclude fees,
slippage, swaps, conversion changes and margin sufficiency. This is a standalone
calculator; it does not change existing sizing or enable orders.

## Persistence and prospective use

The only production store permitted by `ShadowStore` is
runtime/v35_session_shadow_r1/shadow.sqlite3. Fixture paths must be outside the project.
Identity is observation_id + shadow version. UPDATE/DELETE triggers abort. Repeated
identical payloads are idempotent; conflicting identities fail without rewriting.
REPLAY and PROSPECTIVE are explicit kinds. Recorder failures are isolated by
`isolated_observe`; it returns recorder diagnostics only and must never be consumed
by decision logic. It is intentionally not wired into the stopped collector during
this mission. Prospective integration requires independent recovery authorization.

`prospective_status` reads the store and tracks 50/100/250/500 observations, unique
days, sessions, volatility regimes and candidate counts. Replays do not count.
Threshold counts never certify an edge. Cost-adjusted validation, independent
confidence intervals and portfolio drawdown remain unestablished.

Replay report metrics condition *existing strategy outcomes* on present/absent/
unknown shadow features. They are not candidate-specific entry/exit performance.
Sequential gross-R drawdown is not an actual overlapping-position equity curve.
Warnings cover small N, two-day clustering, repeated observations of one event,
overlapping definitions, multiple comparisons, selection bias and incomplete costs.
No historical threshold optimization, learning, outcome mutation or relabeling occurs.
