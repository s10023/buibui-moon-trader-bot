<script lang="ts">
  import { getOhlcv, getSignals, getFib, type CandleRow, type FibResponse, type FundingRow, type OiRow, type SignalRow } from "../api";
  import { symbols } from "../stores/config";
  import { strategyNames } from "../stores/strategies";
  import { selectedSymbol, selectSymbol } from "../stores/watchlist";
  import CandleChart from "../components/CandleChart.svelte";
  import ErrorBanner from "../components/ErrorBanner.svelte";
  import LoadingSpinner from "../components/LoadingSpinner.svelte";

  const TIMEFRAMES = ["15m", "1h", "4h", "1d"];

  // B6: Short display labels for strategy pills
  const STRATEGY_LABELS: Record<string, string> = {
    bos:                  "BOS",
    fvg:                  "FVG",
    orb:                  "ORB",
    doji:                 "Doji",
    engulfing:            "Engulfing",
    pin_bar:              "Pin Bar",
    inside_bar:           "Inside Bar",
    marubozu:             "Marubozu",
    wick_fill:            "Wick Fill",
    trend_day:            "Trend Day",
    eqh_eql:              "EQH/EQL",
    ote_entry:            "OTE Entry",
    fib_golden_zone:      "Fib Zone",
    cvd_divergence:       "CVD Div",
    smt_divergence:       "SMT Div",
    order_block:          "Ord Block",
    liquidity_sweep:      "Liq Sweep",
    funding_reversion:    "Fund Rev",
    hammer_hanging_man:   "Hammer/HM",
    morning_evening_star: "M/E Star",
  };

  // C7: Candlestick pattern group
  const CANDLESTICK_STRATEGIES = new Set([
    "doji", "engulfing", "hammer_hanging_man", "inside_bar",
    "marubozu", "morning_evening_star", "pin_bar",
  ]);

  function stratLabel(name: string): string {
    return STRATEGY_LABELS[name] ?? name;
  }

  let candlestickExpanded = $state(false);

  let symbol = $state($selectedSymbol);
  let timeframe = $state("4h");
  let days = $state(90);
  let selectedStrategies = $state<string[]>([]);
  let showFunding = $state(false);
  let showOI = $state(false);
  let showFib = $state(false);

  // Indicator toggles (C2)
  let showEMA20 = $state(false);
  let showEMA50 = $state(false);
  let showEMA200 = $state(false);
  let showRSI = $state(false);

  let candles = $state<CandleRow[]>([]);
  let signals = $state<SignalRow[]>([]);
  let funding = $state<FundingRow[] | null>(null);
  let oi = $state<OiRow[] | null>(null);
  let fibLevels = $state<FibResponse | null>(null);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let loaded = $state(false);

  // Keep localStorage in sync when symbol changes programmatically
  $effect(() => {
    selectSymbol(symbol);
  });

  // Auto-select + auto-load on mount:
  // - If no saved symbol, pick the first from the watchlist
  // - If symbol is set but chart not yet loaded, trigger load
  $effect(() => {
    const syms = $symbols;
    if (syms.length === 0) return;
    if (!symbol) symbol = syms[0];
    if (symbol && !loaded && !loading) void load();
  });

  async function load(): Promise<void> {
    loading = true;
    error = null;
    const end_ms = Date.now();
    const start_ms = end_ms - days * 24 * 60 * 60 * 1000;
    try {
      const [ohlcvResp, sigResp, fibResp] = await Promise.all([
        getOhlcv({ symbol, timeframe, start_ms, end_ms, include_funding: showFunding, include_oi: showOI }),
        selectedStrategies.length > 0
          ? getSignals({ symbol, timeframe, start_ms, end_ms, strategies: selectedStrategies })
          : Promise.resolve({ signals: [] }),
        showFib
          ? getFib({ symbol, timeframe, start_ms, end_ms }).catch(() => null)
          : Promise.resolve(null),
      ]);
      candles = ohlcvResp.candles;
      signals = sigResp.signals;
      funding = ohlcvResp.funding;
      oi = ohlcvResp.oi;
      fibLevels = fibResp ?? null;
      loaded = true;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  // Clicking a watchlist symbol immediately loads the chart
  function handleSymbolClick(sym: string): void {
    symbol = sym;
    void load();
  }

  function toggleStrategy(name: string): void {
    selectedStrategies = selectedStrategies.includes(name)
      ? selectedStrategies.filter((s) => s !== name)
      : [...selectedStrategies, name];
    void load();
  }
</script>

<div class="chart-page">
  <!-- B2: Watchlist sidebar -->
  <aside class="watchlist">
    <div class="watchlist-header">Watchlist</div>
    {#each $symbols as sym}
      <button
        class="watchlist-item"
        class:active={symbol === sym}
        onclick={() => handleSymbolClick(sym)}
      >{sym.replace("USDT", "")}</button>
    {/each}
  </aside>

  <!-- Main chart area -->
  <div class="main">
    {#if error}<ErrorBanner {error} />{/if}

    <div class="controls">
      <div class="form-row">
        <label>Timeframe
          <select bind:value={timeframe} onchange={() => void load()}>
            {#each TIMEFRAMES as tf}<option>{tf}</option>{/each}
          </select>
        </label>
        <label>Days
          <input type="number" bind:value={days} min="7" max="365" onchange={() => void load()} />
        </label>
        <label class="checkbox-label">
          <input type="checkbox" bind:checked={showFunding} onchange={() => void load()} />
          <span>Funding</span>
        </label>
        <label class="checkbox-label">
          <input type="checkbox" bind:checked={showOI} onchange={() => void load()} />
          <span>OI</span>
        </label>
        <label class="checkbox-label">
          <input type="checkbox" bind:checked={showFib} onchange={() => void load()} />
          <span>Fib</span>
        </label>
      </div>

      <!-- C2: Indicator toggles — pill buttons matching strategy pill style -->
      <div class="form-row indicators-row">
        <span class="section-label">Indicators</span>
        <button class="pill" class:active={showEMA20} onclick={() => showEMA20 = !showEMA20}>EMA 20</button>
        <button class="pill" class:active={showEMA50} onclick={() => showEMA50 = !showEMA50}>EMA 50</button>
        <button class="pill" class:active={showEMA200} onclick={() => showEMA200 = !showEMA200}>EMA 200</button>
        <button class="pill" class:active={showRSI} onclick={() => showRSI = !showRSI}>RSI 14</button>
      </div>

      {#if $strategyNames.length > 0}
        {@const nonCandlestick = $strategyNames.filter((n) => !CANDLESTICK_STRATEGIES.has(n))}
        {@const candlestickNames = $strategyNames.filter((n) => CANDLESTICK_STRATEGIES.has(n))}
        <div class="pill-row">
          {#each nonCandlestick as name}
            <button
              class="pill"
              class:active={selectedStrategies.includes(name)}
              onclick={() => toggleStrategy(name)}
            >{stratLabel(name)}</button>
          {/each}
          {#if candlestickNames.length > 0}
            <button
              class="pill group-toggle"
              class:active={candlestickExpanded}
              onclick={() => { candlestickExpanded = !candlestickExpanded; }}
              title="Candlestick patterns"
            >Candles {candlestickExpanded ? "▾" : "▸"}</button>
          {/if}
        </div>
        {#if candlestickExpanded && candlestickNames.length > 0}
          <div class="pill-row pill-group-inner">
            {#each candlestickNames as name}
              <button
                class="pill"
                class:active={selectedStrategies.includes(name)}
                onclick={() => toggleStrategy(name)}
              >{stratLabel(name)}</button>
            {/each}
          </div>
        {/if}
      {/if}
    </div>

    {#if loading}
      <LoadingSpinner label="Loading chart data..." />
    {:else if loaded}
      <div class="chart-frame">
        <CandleChart
          {candles}
          {signals}
          {symbol}
          {timeframe}
          {funding}
          {showFunding}
          {oi}
          {showOI}
          {showFib}
          {fibLevels}
          {showEMA20}
          {showEMA50}
          {showEMA200}
          {showRSI}
        />
      </div>
      <div class="chart-meta">
        <span class="sym-label">{symbol}</span>
        <span class="sep">·</span>
        <span>{candles.length} candles</span>
        <span class="sep">·</span>
        <span class={signals.length > 0 ? "accent" : "muted"}>{signals.length} signals</span>
      </div>
    {:else}
      <p class="hint">// select a symbol from the watchlist</p>
    {/if}
  </div>
</div>

<style>
  .chart-page {
    display: flex;
    gap: 12px;
    align-items: flex-start;
  }

  /* ── Watchlist sidebar ── */
  .watchlist {
    width: 108px;
    flex-shrink: 0;
    background: var(--bg-panel);
    border: 1px solid var(--border-dim);
    border-radius: 4px;
    overflow: hidden;
  }

  .watchlist-header {
    font-size: 9px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--muted);
    padding: 8px 10px 6px;
    border-bottom: 1px solid var(--border-dim);
  }

  .watchlist-item {
    display: block;
    width: 100%;
    text-align: left;
    padding: 7px 10px;
    font-size: 11px;
    font-family: inherit;
    letter-spacing: 0.04em;
    color: var(--text);
    background: transparent;
    border: none;
    border-bottom: 1px solid var(--border-dim);
    cursor: pointer;
    transition: background 80ms, color 80ms;
  }

  .watchlist-item:last-child { border-bottom: none; }

  .watchlist-item:hover {
    background: rgba(88, 166, 255, 0.06);
    color: var(--accent);
  }

  .watchlist-item.active {
    box-shadow: inset 2px 0 0 var(--accent);
    color: var(--accent);
    font-weight: 600;
    background: rgba(0, 212, 170, 0.04);
  }

  /* ── Main area ── */
  .main { flex: 1; min-width: 0; }

  .controls {
    background: var(--bg-panel);
    border: 1px solid var(--border-dim);
    border-radius: 4px;
    padding: 14px 16px;
    margin-bottom: 14px;
  }

  .controls .form-row { margin-bottom: 10px; }
  .controls .form-row:last-child { margin-bottom: 0; }

  .section-label {
    font-size: 9px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--muted);
    align-self: center;
    margin-right: 2px;
  }

  .indicators-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
  }

  .checkbox-label {
    flex-direction: row !important;
    align-items: center;
    gap: 6px;
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
    cursor: pointer;
  }

  .checkbox-label input { width: auto; }

  .pill-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .pill {
    font-size: 10px;
    padding: 3px 10px;
    border-radius: 2px;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--muted);
    letter-spacing: 0.06em;
    text-transform: none;
    font-weight: 400;
    transition: all 100ms;
  }

  .pill:hover { border-color: var(--accent); color: var(--text); background: transparent; }

  .pill.active {
    background: var(--accent-dim);
    border-color: var(--accent);
    color: var(--accent);
  }

  .group-toggle {
    opacity: 0.7;
    letter-spacing: 0.04em;
  }

  .group-toggle.active { opacity: 1; }

  .pill-group-inner {
    margin-top: 4px;
    padding-left: 10px;
    border-left: 1px solid var(--border-dim);
  }

  .chart-frame {
    border: 1px solid var(--border-dim);
    border-radius: 4px;
    overflow: hidden;
  }

  .chart-meta {
    padding: 6px 2px;
    font-size: 10px;
    color: var(--muted);
    display: flex;
    gap: 6px;
    letter-spacing: 0.05em;
  }

  .sym-label { color: var(--text); font-weight: 600; }
  .accent { color: var(--accent); }
  .sep { opacity: 0.4; }

  .hint {
    color: var(--muted);
    font-size: 12px;
    letter-spacing: 0.05em;
    padding: 20px 0;
  }
</style>
