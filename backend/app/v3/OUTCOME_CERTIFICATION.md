# V3.4 outcome certification contract

The historical observations and `V3_AUTOMATIC_OUTCOMES_v3_outcome_r1.jsonl`
are immutable inputs. The original 31 V3.4 setups / 155 contracts are retained
and flagged `LEGACY_UNCERTIFIED`; they are not certified training labels.

## Permitted inputs and outputs

`certification.project_setup` is a positive allowlist. The resolver receives
only observation identity, unchanged strategy eligibility/direction/families,
reporting groups, explicit timing provenance, and completed M15 ATR metadata.
It never receives any V3.1–V3.4 research snapshot tree. In particular, synthetic
future outcomes, example return series, placeholder calibration/performance,
hardcoded attribution/exposure, provider data fetched after decision time,
and future-target schema declarations cannot become labels or model features.
`EXCLUSIONS` classifies the known fields. No model fitting or training-target
export is enabled; `training_targets` fails closed. Historical fields remain
untouched. New snapshots use unavailable markers instead of invented results.

Only certified journal records accepted by `require_certified_outcome` may enter
the new performance report. The legacy resolver refuses V3.4 observations.
Legacy diagnostic reports are historical artifacts, not certification sources.

## Timing and coverage

UTC offsets must be explicit and reconcile raw broker bar/tick epochs. Bar time
is not decision time. Entry must be the first valid tick strictly after the
authoritative decision timestamp. Quotes at or before decision cannot enter.
Naive times, inconsistent offsets, future/incomplete feature candles and
decision quotes older than five seconds are rejected. Missing old metadata is
never guessed. Strategy feature time can precede decision; it cannot follow it.

The base ATR14 × 1.5 stop, BUY/SELL bid/ask sides, six-hour holding period and
1/1.5/2/3/5 RR contracts are unchanged. Certification adds conservative data
coverage gates: entry within 60 seconds, no inter-tick gap above 300 seconds,
timeout quote within 60 seconds of horizon. These are data rejection rules,
not signal thresholds or strategy optimization. No OHLC substitution is used.

The certification policy is a new immutable version/hash. `policy_r` retains
the old fixed TP/SL contract convention; `realized_r` uses the observed exit
quote and can reflect a price crossing beyond the threshold. It is quote-based
PRE_COMMISSION, not an actual broker fill. Commission, actual slippage and swap
are unavailable, with `net_r=null`; unavailable costs are never recorded as zero.

## Bounded processing and persistence

Observations are streamed once into a compact SQLite setup/byte-offset index,
with 100-row ingestion checkpoints. One research row is retained at a time and
an explicit 4 MiB row ceiling fails closed. Feasibility uses batches of 25 and
15-minute tick windows, limited to the individual setup horizon. Tick windows
are cached on disk; no broad multi-week tick array or all-observation list is
created. SQLite cache is capped at 8 MiB and temporary sorting uses disk.

The certified SQLite journal is authoritative. Each setup's contracts commit
in one FULL-synchronous transaction with unique setup/RR/policy identity and
deterministic IDs. Conflicting replay raises an error. The separate certified
JSONL is a durable atomic projection; a crash cannot lose the journal transaction
and rerunning export repairs a missing/stale projection without duplication.
Original JSONL files are never opened for write. Candidate calculations from
feasibility are explicitly staging data until the pilot gate passes.

Pending = eligible − certified 1R resolved. Uncertifiable and failed are pending
subsets; timeout is a resolved subset. The counts are not mutually exclusive
columns to sum. `pending_other` closes the pending partition. Partial RR results
may be retained where their complete price path is certified; unresolved higher
RR contracts never inflate the independent 1R count.

## Lifecycle and operation

Dot-source `ops/windows/v3-research-safety.ps1` before any worker invocation.
Every worker requires all seven explicit paper/read-only/no-training/no-order
flags. Neither repair worker exposes a trading API or imports model fitting.

Run `backend/scripts/v34_recertify.py index`, then `feasibility`. After tests,
run `pilot`, then `v34_certify_pilot.py`; only a successful independent tick-path
replay enables `commit`. `serve` retains the same gate, retries transient MT5
failures and remains alive while idle. No continuous worker is required to stay
running after this campaign.

Existing locks require a separate PID AND logical-worker command-line audit;
TTL alone never authorizes recovery. Archive stale locks and their proof.
`v3_worker_health.py` derives status from PID, lock identity and heartbeat age;
a saved RUNNING string alone is never evidence of health. Abrupt OS termination
cannot atomically rewrite its own state file, so health consumers must use this
derived status. Graceful exit records STOPPED/FAILED and releases only its own
lock. Long-duration collector reliability remains a separate observation gate.
