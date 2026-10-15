export type Candle = { time: number; open: number; high: number; low: number; close: number };
export type Plan = { entry: number; stop: number; target: number; risk_distance: number; reward_distance: number; rr: number };
export type Zone = { kind: string; lower: number; upper: number; broken: boolean; certification: string };
export type Feature = { feature: string; value: unknown; source: string | null; source_timestamp: string | null; acquisition_timestamp: string | null; decision_timestamp: string; status: string; reason: string };
export type Setup = { id: string; decision_time: string; direction: string; symbol: string; timeframe: string; regime: string | null; session: string | null; families: string[]; plan: Plan | null; plan_reason: string; reasons: string[]; zones: Zone[]; features: Feature[]; vector: { status: string; reasons: string[] }; snapshot_hash: string };
export type Metadata = { symbol: string; account_currency: string; tick_size: number; tick_value_loss: number; volume_min: number; volume_max: number; volume_step: number; digits: number; point: number; contract_size: number };
export type Snapshot = {
  campaign: { campaign_id: string; cutoff: string; policy_version: string; policy_hash: string; observations: number; eligible: number; no_trade: number; resolved: number; pending: number; choices: { id: string; decision: string; eligible: boolean }[]; setup: Setup | null; readiness: Record<string, boolean>; bucket_plan: Record<string, number[]>; dates: [string, number, string][]; training_allowed: false };
  market: { symbol: string; timeframe: string; checked_at: string; status: string; display_mode: string; chart_source?: string; chart_captured_at?: string; connected: boolean; selected: boolean; tick_timestamp: string | null; tick_age: number | null; freshness_limit: number; spread?: number; bid?: number; ask?: number; latest_completed_candle?: string; candles: Candle[]; timeframes: Record<string, boolean>; metadata: Metadata | null; error?: string; costs?: { commission: string; swap: string; slippage: string; basis: string } };
};
export function geometry(direction: string, entry: number, stop: number, target: number): Plan | null {
  if (![entry, stop, target].every(x => Number.isFinite(x) && x > 0)) return null;
  if (!(direction === 'BUY' ? stop < entry && entry < target : direction === 'SELL' ? target < entry && entry < stop : false)) return null;
  const risk_distance = Math.abs(entry - stop), reward_distance = Math.abs(target - entry);
  return { entry, stop, target, risk_distance, reward_distance, rr: reward_distance / risk_distance };
}
export function positionSize(balance: number, riskPercent: number, entry: number, stop: number, metadata: Metadata | null) {
  if (!metadata || ![balance, riskPercent, entry, stop, metadata.tick_size, metadata.tick_value_loss, metadata.volume_step, metadata.volume_max, metadata.volume_min].every(x => Number.isFinite(x) && x > 0) || riskPercent > 100 || entry === stop || !metadata.account_currency) return null;
  const riskAmount = balance * riskPercent / 100, distance = Math.abs(entry - stop);
  const raw = riskAmount / (distance / metadata.tick_size * metadata.tick_value_loss);
  const lots = Math.floor((Math.min(raw, metadata.volume_max) + 1e-10) / metadata.volume_step) * metadata.volume_step;
  if (lots < metadata.volume_min) return null;
  return { riskAmount, distance, lots, currency: metadata.account_currency };
}
export function decisionIndex(candles: Candle[], epoch: number): number | null {
  if (!candles.length || !Number.isFinite(epoch)) return null;
  for (let i = 0; i < candles.length - 1; i++) {
    if (epoch >= candles[i].time && epoch <= candles[i + 1].time)
      return i + (epoch - candles[i].time) / (candles[i + 1].time - candles[i].time);
  }
  return epoch === candles[candles.length - 1].time ? candles.length - 1 : null;
}
