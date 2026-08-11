// Typed API client — interfaces match real FastAPI response models.

// ── Config ────────────────────────────────────────────────────────────────────

export interface SymbolConfig {
  leverage: number;
  sl_percent: number;
  smt_secondary?: string;
}

export type ConfigResponse = Record<string, SymbolConfig>;

// ── Strategies ────────────────────────────────────────────────────────────────

export interface ParamSpec {
  name: string;
  param_type: "int" | "float";
  default: number;
  min_val: number;
  max_val: number;
  description: string;
}

export interface StrategySpec {
  name: string;
  description: string;
  confidence: number | Record<string, number>;
  params: ParamSpec[];
  requires_funding: boolean;
  requires_secondary: boolean;
}

export type StrategiesResponse = Record<string, StrategySpec>;

// ── OHLCV ─────────────────────────────────────────────────────────────────────

export interface CandleRow {
  open_time: number; // Unix ms
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  taker_buy_volume: number | null;
}

export interface FundingRow {
  funding_time: number; // Unix ms
  funding_rate: number;
}

export interface OiRow {
  timestamp: number; // Unix ms
  oi_usd: number;
}

export interface OhlcvResponse {
  candles: CandleRow[];
  funding: FundingRow[] | null;
  oi: OiRow[] | null;
}

// ── Fibonacci ─────────────────────────────────────────────────────────────────

export interface FibLevel {
  label: string;
  price: number;
  golden: boolean;
}

export interface FibResponse {
  swing_low: number;
  swing_high: number;
  swing_start_ms: number;
  levels: FibLevel[];
}

// ── Signals ───────────────────────────────────────────────────────────────────

export interface SignalRow {
  open_time: number;
  direction: string;
  strategy: string;
  reason: string;
  sl_price: number;
  entry_price: number | null;
  confidence: number;
  context: string;
}

export interface SignalsResponse {
  signals: SignalRow[];
}

// ── Backtest ──────────────────────────────────────────────────────────────────

export interface BacktestRunSummary {
  run_id: string;
  symbol: string;
  timeframe: string;
  strategy: string;
  days: number;
  sl_pct: number;
  tp_r: number;
  fee_pct: number;
  day_filter: string;
  adr_suppress_threshold: number | null;
  closed_trades: number;
  win_count: number;
  loss_count: number;
  win_rate: number;
  avg_r: number;
  total_r: number;
  max_drawdown_r: number;
  recovery_factor: number | null;
  sweep_id: string | null;
  run_at_ms: number;
  long_closed_trades: number | null;
  long_win_count: number | null;
  long_win_rate: number | null;
  long_avg_r: number | null;
  long_total_r: number | null;
  short_closed_trades: number | null;
  short_win_count: number | null;
  short_win_rate: number | null;
  short_avg_r: number | null;
  short_total_r: number | null;
  stars: number | null;
  long_stars: number | null;
  short_stars: number | null;
}

export interface TradeModel {
  signal_time: number;
  entry_time: number;
  entry_price: number;
  direction: string;
  sl_price: number;
  tp_price: number;
  exit_time: number | null;
  exit_price: number | null;
  outcome: string;
  pnl_r: number | null;
}

export interface BacktestResponse {
  symbol: string;
  timeframe: string;
  strategy: string;
  total_trades: number;
  closed_trades: number;
  win_count: number;
  loss_count: number;
  win_rate: number;
  avg_r: number;
  total_r: number;
  max_drawdown_r: number;
  recovery_factor: number;
  long_closed_trades: number;
  long_win_count: number;
  long_win_rate: number | null;
  long_avg_r: number | null;
  long_total_r: number | null;
  short_closed_trades: number;
  short_win_count: number;
  short_win_rate: number | null;
  short_avg_r: number | null;
  short_total_r: number | null;
  trades: TradeModel[];
}

// ── Prices ────────────────────────────────────────────────────────────────────

export interface PriceRow {
  symbol: string;
  last_price: string;
  change_15m: string;
  change_1h: string;
  change_4h: string;
  change_asia: string;
  change_24h: string;
}

export interface PricesResponse {
  prices: PriceRow[];
}

// ── Positions ─────────────────────────────────────────────────────────────────

export interface PositionRow {
  symbol: string;
  side: string;
  position_side: string;
  leverage: number | null;
  margin_type: string | null;
  entry_price: number | null;
  mark_price: number | null;
  liq_price: number | null;
  margin: number | null;
  notional: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  risk_pct: string | null;
  tp_price: number | null;
  sl_price: number | null;
  sl_size: string | null;
  sl_usd: string | null;
}

export interface PositionsResponse {
  positions: PositionRow[];
  wallet_balance: number;
  unrealized_pnl: number;
  available_balance: number;
  total_risk_usd: number;
}

// ── SSE stream shapes ─────────────────────────────────────────────────────────

// /api/stream/prices emits: PriceRow[] (same as REST PriceRow)
export type PriceStreamFrame = PriceRow[];

// /api/stream/positions emits: PositionsResponse (same shape)
export type PositionsStreamFrame = PositionsResponse;

// ── Core fetch helper ─────────────────────────────────────────────────────────

const TOKEN = (import.meta.env.VITE_API_TOKEN as string | undefined) ?? "";

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}),
    ...(options.headers as Record<string, string> | undefined),
  };
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    const text = await res.text();
    let detail = text;
    try {
      const json = JSON.parse(text) as { detail?: string };
      if (json.detail) detail = json.detail;
    } catch {
      /* not JSON — use raw text */
    }
    throw new Error(`API ${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

// ── Named helpers ─────────────────────────────────────────────────────────────

// ── Active Config ─────────────────────────────────────────────────────────────

export interface StrategyParamsModel {
  tp_r: number | null;
  sl_pct: number | null;
  tp_r_per_tf: Record<string, number>;
}

export interface ActiveConfigResponse {
  config_name: string | null;
  symbols: string[] | null;
  timeframes: string[];
  strategies: string[] | null;
  day_filter: string;
  tp_r: number;
  sl_pct: number;
  fee_pct: number;
  min_sl_pct: number;
  adr_suppress_threshold: number | null;
  strategy_params: Record<string, StrategyParamsModel>;
  min_trades: number;
  min_trades_per_tf: Record<string, number>;
}

export const getConfig = () => apiFetch<ConfigResponse>("/api/config");
export const getStrategies = (configName?: string | null) => {
  const path = configName ? `/api/strategies?config=${encodeURIComponent(configName)}` : "/api/strategies";
  return apiFetch<StrategiesResponse>(path);
};
export const getActiveConfig = () =>
  apiFetch<ActiveConfigResponse>("/api/active-config");

export const getOhlcv = (params: {
  symbol: string;
  timeframe: string;
  start_ms: number;
  end_ms: number;
  include_funding?: boolean;
  include_oi?: boolean;
}) => {
  const q = new URLSearchParams({
    symbol: params.symbol,
    timeframe: params.timeframe,
    start_ms: String(params.start_ms),
    end_ms: String(params.end_ms),
    ...(params.include_funding ? { include_funding: "true" } : {}),
    ...(params.include_oi ? { include_oi: "true" } : {}),
  });
  return apiFetch<OhlcvResponse>(`/api/ohlcv?${q}`);
};

export const getLiveCandle = (params: { symbol: string; timeframe: string }) => {
  const q = new URLSearchParams({ symbol: params.symbol, timeframe: params.timeframe });
  return apiFetch<CandleRow>(`/api/ohlcv/live?${q}`);
};

export const getSignals = (params: {
  symbol: string;
  timeframe: string;
  start_ms: number;
  end_ms: number;
  strategies: string[];
}) => apiFetch<SignalsResponse>("/api/signals", { method: "POST", body: JSON.stringify(params) });

export const getSignalsHistory = (params: {
  symbol: string;
  timeframe: string;
  start_ms: number;
  end_ms: number;
}) => {
  const q = new URLSearchParams({
    symbol: params.symbol,
    timeframe: params.timeframe,
    start_ms: String(params.start_ms),
    end_ms: String(params.end_ms),
  });
  return apiFetch<SignalsResponse>(`/api/signals/history?${q}`);
};

export const getBacktestRuns = () =>
  apiFetch<BacktestRunSummary[]>("/api/backtest/runs");

export interface DigestResult {
  columns: string[];
  rows: (string | number | null)[][];
}

export const getBacktestAnalysis = (
  query: string,
  minTrades: number = 5,
  topN: number = 20,
  useConfig: boolean = false,
) => {
  const q = new URLSearchParams({
    query,
    min_trades: String(minTrades),
    top_n: String(topN),
    use_config: String(useConfig),
  });
  return apiFetch<DigestResult>(`/api/backtest/analysis?${q}`);
};

export const runBacktest = (params: {
  symbol: string;
  timeframe: string;
  strategy: string;
  days: number;
  sl_pct: number;
  tp_r: number;
  fee_pct?: number;
  secondary_symbol?: string;
  [key: string]: unknown;
}) =>
  apiFetch<BacktestResponse>("/api/backtest", {
    method: "POST",
    body: JSON.stringify(params),
  });

export const getFib = (params: {
  symbol: string;
  timeframe: string;
  start_ms: number;
  end_ms: number;
}) => {
  const q = new URLSearchParams({
    symbol: params.symbol,
    timeframe: params.timeframe,
    start_ms: String(params.start_ms),
    end_ms: String(params.end_ms),
  });
  return apiFetch<FibResponse>(`/api/fib?${q}`);
};

// ── Structural Zones ──────────────────────────────────────────────────────────

export interface ZoneBox {
  zone_type: "fvg" | "ob" | "fib_zone" | "ote";
  direction: "bull" | "bear";
  zone_low: number;
  zone_high: number;
  start_ms: number;
  close_ms: number | null;
  active: boolean;
}

export interface ZoneLine {
  zone_type: "eqh" | "eql" | "bos";
  direction: "bull" | "bear";
  price: number;
  start_ms: number;
  close_ms: number | null;
  label: string;
  active: boolean;
}

export interface SwingPoint {
  swing_type: "high" | "low";
  price: number;
  time_ms: number;
}

export interface ZonesResponse {
  boxes: ZoneBox[];
  lines: ZoneLine[];
  swings: SwingPoint[];
}

export const getZones = (params: {
  symbol: string;
  timeframe: string;
  start_ms: number;
  end_ms: number;
}) => {
  const q = new URLSearchParams({
    symbol: params.symbol,
    timeframe: params.timeframe,
    start_ms: String(params.start_ms),
    end_ms: String(params.end_ms),
  });
  return apiFetch<ZonesResponse>(`/api/zones?${q}`);
};

// ── Stats ─────────────────────────────────────────────────────────────────────

export interface P1P2DOWRow {
  dow: string;
  p1_low_pct: number;
  sample_days: number;
}

export interface P1P2Response {
  overall_p1_low_pct: number;
  by_dow: P1P2DOWRow[];
  sample_days: number;
  p1_strong_pct: number;
}

export interface HourlyExtremeRow {
  hour_myt: number;
  high_pct: number;
  low_pct: number;
}

export interface ADRResponse {
  adr_14: number;
  adr_30: number;
  today_range_pct: number | null;
  today_consumed_pct: number | null;
}

export interface DOWPatternRow {
  dow: string;
  avg_range_pct: number;
  bull_pct: number;
  sample_days: number;
  avg_return_pct: number;
  strong_high_pct: number;
  strong_low_pct: number;
}

export interface SessionRow {
  session: string;
  high_pct: number;
  low_pct: number;
}

export interface WeeklyP1P2Response {
  overall_p1_low_pct: number;
  low_day: string;
  high_day: string;
  sample_weeks: number;
  low_by_dow: Record<string, number>;
  high_by_dow: Record<string, number>;
}

export interface WeeklyP2TimingResponse {
  low_still_ahead_by_dow: Record<string, number>;
  high_still_ahead_by_dow: Record<string, number>;
  low_flip_risk_by_dow: Record<string, number>;
  high_flip_risk_by_dow: Record<string, number>;
}

export interface WeeklyCurrentStateResponse {
  current_isodow: number;
  current_dow: string;
  weekly_open: number;
  current_price: number;
  move_pct: number;
  move_bucket: "small" | "medium" | "large";
  low_still_ahead_conditioned: number | null;
  high_still_ahead_conditioned: number | null;
}

export interface FlipRiskConditionedRow {
  p1_direction: string;
  isodow: number;
  dow_label: string;
  flip_pct: number;
  sample_count: number;
}

export interface WeeklyFlipRiskConditionedResponse {
  rows: FlipRiskConditionedRow[];
}

export interface ConeComboResponse {
  direction: string;
  weekday: string;
  n: number;
  bands: number[][];
  low_in_by: number[];
  high_in_by: number[];
  mae_p: number[];
  mfe_p: number[];
  high_piv: number[];
  low_piv: number[];
}

export interface PathConeResponse {
  combos: Record<string, ConeComboResponse>;
  total_days: number;
}

export interface TodayPathResponse {
  points: number[];
  elapsed_h: number;
  adr14_today: number;
  today_open: number;
}

export interface WeeklyConeCombo {
  direction: string;
  n: number;
  bands: number[][];
  low_in_by: number[];
  high_in_by: number[];
  mae_p: number[];
  mfe_p: number[];
  high_piv: number[];
  low_piv: number[];
}

export interface WeeklyCone {
  combos: Record<string, WeeklyConeCombo>;
  total_weeks: number;
}

export interface CurrentWeekPath {
  points: number[];
  elapsed_h: number;
  awr14_current: number;
  week_open: number;
}

export interface WeeklyWickPercentileResponse {
  current_wick_of_adr: number | null;
  exceedance_pct: number | null;
  p1_direction: string | null;
  sample_count: number;
}

export interface StatsResponse {
  symbol: string;
  days: number;
  computed_at_ms: number;
  p1p2: P1P2Response;
  hourly_extremes: HourlyExtremeRow[];
  adr: ADRResponse;
  dow_patterns: DOWPatternRow[];
  sessions: SessionRow[];
  weekly_p1p2: WeeklyP1P2Response;
  weekly_p2_timing: WeeklyP2TimingResponse;
  weekly_current_state: WeeklyCurrentStateResponse | null;
  weekly_flip_risk_conditioned: WeeklyFlipRiskConditionedResponse | null;
  path_cone: PathConeResponse;
  today_path: TodayPathResponse | null;
  weekly_wick_percentile: WeeklyWickPercentileResponse | null;
  weekly_cone: WeeklyCone | null;
  current_week_path: CurrentWeekPath | null;
}

export const getStats = (symbol: string, days: number = 180) =>
  apiFetch<StatsResponse>(`/api/stats/${symbol}?days=${days}`);

// ── Live outcomes (cross-symbol signal_alert_outcomes ledger) ────────────────

export interface LiveOutcomesRollup {
  total_rows: number;
  resolved: number;
  open: number;
  open_no_tp: number;
  wins: number;
  losses: number;
  expired: number;
}

export interface LiveOutcomeCell {
  strategy: string;
  tf: string;
  direction: string;
  n: number;
  wins: number;
  losses: number;
  expired: number;
  win_rate: number | null;
  avg_r: number | null;
}

export interface LiveOutcomeStrategyRow {
  strategy: string;
  n: number;
  wins: number;
  losses: number;
  expired: number;
  win_rate: number | null;
  avg_r: number | null;
}

export interface LiveOutcomeSymbolRow {
  symbol: string;
  n: number;
}

export interface LiveOpenPosition {
  signal_id: string;
  symbol: string;
  strategy: string;
  tf: string;
  direction: string;
  fired_at_ms: number;
  entry_price: number | null;
  sl_price: number | null;
  tp_price: number | null;
  mark: number | null;
  /** GROSS of costs, unlike the net outcome_r in the tables. */
  unrealized_r: number | null;
  dist_sl_pct: number | null;
  dist_tp_pct: number | null;
}

export interface LiveOpenPositionsResponse {
  symbol: string | null;
  marks_ok: boolean;
  marked_at_ms: number;
  positions: LiveOpenPosition[];
}

export interface LiveOutcomesResponse {
  days: number;
  min_n: number;
  rollup: LiveOutcomesRollup;
  cells: LiveOutcomeCell[];
  by_strategy: LiveOutcomeStrategyRow[];
  symbols: LiveOutcomeSymbolRow[];
}

export const getLiveOutcomes = (
  days: number = 30,
  minN: number = 1,
  symbol: string | null = null,
) =>
  apiFetch<LiveOutcomesResponse>(
    `/api/live-outcomes?days=${days}&min_n=${minN}` +
      (symbol ? `&symbol=${encodeURIComponent(symbol)}` : ""),
  );

export const getLiveOutcomesOpen = (symbol: string | null = null) =>
  apiFetch<LiveOpenPositionsResponse>(
    `/api/live-outcomes/open` +
      (symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""),
  );

// ── Daily Brief ───────────────────────────────────────────────────────────────

export interface BriefLevelRow {
  name: string;
  price: number;
  dist_atr: number;
  swept: boolean;
}

export interface BriefZoneRow {
  tf: string;
  zone_type: string;
  direction: string;
  zone_low: number;
  zone_high: number;
  dist_atr: number;
  inside: boolean;
}

export interface BriefSeasonalityStrip {
  dow: string;
  bull_pct: number | null;
  avg_range_pct: number | null;
  median_range_pct: number | null;
  sample_days: number | null;
  high_session: string | null;
  high_session_pct: number | null;
  low_session: string | null;
  low_session_pct: number | null;
  weekly_low_still_ahead: number | null;
  weekly_high_still_ahead: number | null;
  typical_low_day: string | null;
  typical_high_day: string | null;
}

export interface BriefEmaState {
  above_20: boolean | null;
  above_50: boolean | null;
  above_200: boolean | null;
  stack: string | null;
  slope_200: string | null;
}

export interface BriefRangeState {
  label: string;
  since_ms: number;
  bars: number;
  range_low: number | null;
  range_high: number | null;
  pos: number | null;
}

export interface BriefMondayState {
  state: string;
  pos: number | null;
}

export interface BriefCandleHit {
  pattern: string;
  direction: string;
}

export interface BriefPaState {
  label: string;
  er: number;
  speed_atr: number;
}

export interface BriefBbState {
  pct_b: number;
  bandwidth: number;
  bw_pctile: number | null;
  squeeze: boolean | null;
}

export interface BriefVwapState {
  weekly_price: number | null;
  weekly_dist_atr: number | null;
  monthly_price: number | null;
  monthly_dist_atr: number | null;
}

export interface BriefProfileState {
  poc: number;
  vah: number;
  val: number;
  vs_value: string;
  poc_dist_atr: number;
}

export interface BriefIndicatorState {
  ema: BriefEmaState | null;
  range_state: BriefRangeState | null;
  monday: BriefMondayState | null;
  candles: BriefCandleHit[] | null;
  pa: BriefPaState | null;
  bb: BriefBbState | null;
  vwap: BriefVwapState | null;
  profile: BriefProfileState | null;
}

export interface BriefSessionClock {
  label: string;
  start_ms: number;
  end_ms: number;
  is_overlap: boolean;
  next_label: string;
  next_start_ms: number;
}

export interface BriefSessionRecapRow {
  session: string;
  start_ms: number;
  end_ms: number;
  open: number;
  high: number;
  low: number;
  close: number;
  net_pct: number;
  net_atr: number | null;
  range_atr: number | null;
  n_bars: number;
  expected_bars: number;
  made_set_high: boolean;
  made_set_low: boolean;
}

export interface BriefSessionTendencyRow {
  session: string;
  high_pct: number;
  low_pct: number;
}

export interface BriefSessionState {
  recap: BriefSessionRecapRow[] | null;
  tendency: BriefSessionTendencyRow[] | null;
}

export interface BriefExternalClusterRow {
  price_lo: number;
  price_hi: number;
  kind: string;
  intensity: string;
  label: string;
  dist_atr: number;
}

export interface BriefExternalSnapshot {
  source: string;
  venue: string | null;
  panel: string;
  window: string | null;
  scope: string | null;
  captured_at_ms: number;
  age_hours: number;
  spot_price_hint: number | null;
  spot_hint_deviation: boolean;
  clusters_above: BriefExternalClusterRow[];
  clusters_below: BriefExternalClusterRow[];
}

export interface BriefExternalState {
  snapshots: BriefExternalSnapshot[];
}

export interface BriefWeeklyState {
  path_direction: string;
  elapsed_h: number;
  total_bars: number;
  norm_now: number;
  pct_conditional: number;
  pct_unconditional: number;
  n_conditional: number;
  n_unconditional: number;
  low_hour: number | null;
  high_hour: number | null;
  low_in_by_now: number;
  /**
   * True when the same-direction cohort could not be resolved distinctly and
   * the conditional pool fell back to the unconditional one. The renderer is
   * TOLD rather than inferring from `path_direction`: a "flat" week has no
   * cohort at all, but a bull/bear combo can also exist while being empty.
   * Both cases must drop the cohort label — presenting the unconditional
   * population under a cohort name misattributes it as conditional.
   */
  conditional_is_fallback: boolean;
}

export interface BriefMonthlyContext {
  mtd_return_pct: number;
  mtd_elapsed_frac: number;
  pct_of_months: number | null;
  n_months: number;
  range_position: number | null;
}

export interface BriefSymbolPanel {
  symbol: string;
  ref_close: number;
  ref_close_ts_ms: number;
  ref_price_source: string;
  atr14: number;
  adr_pct: number | null;
  regime_1d: string;
  regime_4h: string;
  levels_above: BriefLevelRow[];
  levels_below: BriefLevelRow[];
  zones_above: BriefZoneRow[];
  zones_below: BriefZoneRow[];
  seasonality: BriefSeasonalityStrip | null;
  indicators: BriefIndicatorState | null;
  sessions: BriefSessionState | null;
  error: string | null;
  external: BriefExternalState | null;
  weekly: BriefWeeklyState | null;
  monthly: BriefMonthlyContext | null;
}

export interface BriefAuthorPrior {
  author: string;
  n: number;
  hit_rate: number | null;
  avg_r: number | null;
  avg_atr_r: number | null;
  flagged: boolean;
}

// The family prior carries both avg_r and avg_atr_r, matching the author prior
// (see web/api/models/brief.py::PunditFamilyPriorModel).
export interface BriefFamilyPrior {
  family: string;
  direction: string;
  n: number;
  hit_rate: number | null;
  avg_r: number | null;
  avg_atr_r: number | null;
  flagged: boolean;
}

export interface BriefCallRow {
  author: string;
  symbol: string;
  direction: string;
  entry: string;
  target: string;
  horizon: string;
  age_days: number;
  on_panel: boolean;
  prior: BriefAuthorPrior | null;
}

export interface BriefPunditBoard {
  priors_status: string;
  priors_age_days: number | null;
  min_n_marker: number | null;
  ledger_status: string;
  ledger_total: number;
  ledger_skipped: number;
  recent_calls: BriefCallRow[];
  authors: BriefAuthorPrior[];
  families: BriefFamilyPrior[];
}

export interface BriefHealthRow {
  symbol: string;
  tf: string;
  status: string;
  bars_behind: number;
}

export interface BriefHealthReport {
  rows: BriefHealthRow[];
  notes: string[];
  data_ok: boolean;
}

export interface BriefResponse {
  as_of_ms: number;
  day_ahead: string;
  panels: BriefSymbolPanel[];
  session_clock: BriefSessionClock | null;
  pundit: BriefPunditBoard;
  health: BriefHealthReport;
}

export const getBrief = (params?: {
  symbols?: string[];
  days?: number;
  as_of?: string;
}) => {
  const q = new URLSearchParams({
    ...(params?.symbols?.length ? { symbols: params.symbols.join(",") } : {}),
    ...(params?.days ? { days: String(params.days) } : {}),
    ...(params?.as_of ? { as_of: params.as_of } : {}),
  });
  const qs = q.toString();
  return apiFetch<BriefResponse>(qs ? `/api/brief?${qs}` : "/api/brief");
};

// ── SSE helper ────────────────────────────────────────────────────────────────

// EventSource cannot send Authorization headers — token passed as ?token= query param.
export function createSSEStream<T>(
  path: string,
  onMessage: (data: T) => void,
  onError: (err: Event) => void,
  onOpen?: () => void
): () => void {
  const url = TOKEN ? `${path}?token=${encodeURIComponent(TOKEN)}` : path;
  const es = new EventSource(url);
  es.onopen = () => onOpen?.();
  es.onmessage = (e: MessageEvent) => {
    try {
      onMessage(JSON.parse(e.data as string) as T);
    } catch {
      /* ignore malformed frames */
    }
  };
  es.onerror = (err) => {
    onError(err);
  };
  return () => es.close();
}
