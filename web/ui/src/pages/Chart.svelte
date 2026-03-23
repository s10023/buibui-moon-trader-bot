<script lang="ts">
  import { getOhlcv, getSignals, getFib, type CandleRow, type FibLevel, type FundingRow, type OiRow, type SignalRow } from "../api";
  import { symbols } from "../stores/config";
  import { strategyNames } from "../stores/strategies";
  import { selectedSymbol, selectSymbol } from "../stores/watchlist";
  import CandleChart from "../components/CandleChart.svelte";
  import ErrorBanner from "../components/ErrorBanner.svelte";
  import LoadingSpinner from "../components/LoadingSpinner.svelte";

  const TIMEFRAMES = ["15m", "1h", "4h", "1d"];

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
  let fibLevels = $state<FibLevel[] | null>(null);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let loaded = $state(false);

  // Keep localStorage in sync when symbol changes programmatically
  $effect(() => {
    selectSymbol(symbol);
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
      fibLevels = fibResp ? fibResp.levels : null;
      loaded = true;
    } catch (e) {
      error = String(e);
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

      <!-- C2: Indicator toggles -->
      <div class="form-row indicators-row">
        <span class="section-label">Indicators</span>
        <label class="checkbox-label">
          <input type="checkbox" bind:checked={showEMA20} />
          <span>EMA 20</span>
        </label>
        <label class="checkbox-label">
          <input type="checkbox" bind:checked={showEMA50} />
          <span>EMA 50</span>
        </label>
        <label class="checkbox-label">
          <input type="checkbox" bind:checked={showEMA200} />
          <span>EMA 200</span>
        </label>
        <label class="checkbox-label">
          <input type="checkbox" bind:checked={showRSI} />
          <span>RSI 14</span>
        </label>
      </div>

      {#if $strategyNames.length > 0}
        <div class="pill-row">
          {#each $strategyNames as name}
            <button
              class="pill"
              class:active={selectedStrategies.includes(name)}
              onclick={() => toggleStrategy(name)}
            >{name}</button>
          {/each}
        </div>
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
    background: var(--accent-dim);
    color: var(--accent);
    font-weight: 600;
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
