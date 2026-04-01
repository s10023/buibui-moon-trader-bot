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
  closed_trades: number;
  win_count: number;
  loss_count: number;
  win_rate: number;
  avg_r: number;
  total_r: number;
  max_drawdown_r: number;
  sweep_id: string | null;
  run_at_ms: number;
  long_closed_trades: number | null;
  long_win_count: number | null;
  long_win_rate: number | null;
  long_avg_r: number | null;
  short_closed_trades: number | null;
  short_win_count: number | null;
  short_win_rate: number | null;
  short_avg_r: number | null;
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
  long_closed_trades: number;
  long_win_count: number;
  long_win_rate: number | null;
  long_avg_r: number | null;
  short_closed_trades: number;
  short_win_count: number;
  short_win_rate: number | null;
  short_avg_r: number | null;
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
  leverage: number | null;
  entry_price: number | null;
  mark_price: number | null;
  margin: number | null;
  notional: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  risk_pct: string | null;
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

export const getConfig = () => apiFetch<ConfigResponse>("/api/config");
export const getStrategies = () =>
  apiFetch<StrategiesResponse>("/api/strategies");

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
}

export const getStats = (symbol: string, days: number = 180) =>
  apiFetch<StatsResponse>(`/api/stats/${symbol}?days=${days}`);

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
