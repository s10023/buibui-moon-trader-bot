<script lang="ts">
  import { getOhlcv, getSignals, type CandleRow, type SignalRow } from "../api";
  import { symbols } from "../stores/config";
  import { strategyNames } from "../stores/strategies";
  import CandleChart from "../components/CandleChart.svelte";
  import ErrorBanner from "../components/ErrorBanner.svelte";
  import LoadingSpinner from "../components/LoadingSpinner.svelte";

  const TIMEFRAMES = ["15m", "1h", "4h", "1d"];

  let symbol = $state("BTCUSDT");
  let timeframe = $state("4h");
  let days = $state(90);
  let selectedStrategies = $state<string[]>([]);
  let showFunding = $state(false);
  let showFib = $state(false);

  let candles = $state<CandleRow[]>([]);
  let signals = $state<SignalRow[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let loaded = $state(false);

  async function load(): Promise<void> {
    loading = true;
    error = null;
    const end_ms = Date.now();
    const start_ms = end_ms - days * 24 * 60 * 60 * 1000;
    try {
      const [ohlcvResp, sigResp] = await Promise.all([
        getOhlcv({ symbol, timeframe, start_ms, end_ms, include_funding: showFunding }),
        selectedStrategies.length > 0
          ? getSignals({ symbol, timeframe, start_ms, end_ms, strategies: selectedStrategies })
          : Promise.resolve({ signals: [] }),
      ]);
      candles = ohlcvResp.candles;
      signals = sigResp.signals;
      loaded = true;
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  function toggleStrategy(name: string): void {
    selectedStrategies = selectedStrategies.includes(name)
      ? selectedStrategies.filter((s) => s !== name)
      : [...selectedStrategies, name];
  }
</script>

<div class="page">
  <h2>Chart</h2>

  {#if error}<ErrorBanner {error} />{/if}

  <div class="controls">
    <div class="form-row">
      <label>Symbol
        <select bind:value={symbol}>
          {#each $symbols as s}<option>{s}</option>{/each}
        </select>
      </label>
      <label>Timeframe
        <select bind:value={timeframe}>
          {#each TIMEFRAMES as tf}<option>{tf}</option>{/each}
        </select>
      </label>
      <label>Days
        <input type="number" bind:value={days} min="7" max="365" />
      </label>
      <label class="checkbox-label">
        <input type="checkbox" bind:checked={showFunding} />
        <span>Funding</span>
      </label>
      <label class="checkbox-label">
        <input type="checkbox" bind:checked={showFib} />
        <span>Fib</span>
      </label>
      <button disabled={loading} onclick={() => void load()}>Load</button>
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
      <CandleChart {candles} {signals} {symbol} {showFib} />
    </div>
    <div class="chart-meta">
      <span>{candles.length} candles</span>
      <span class="sep">·</span>
      <span class={signals.length > 0 ? "accent" : "muted"}>{signals.length} signals</span>
    </div>
  {:else}
    <p class="hint">// select symbol and timeframe, then load</p>
  {/if}
</div>

<style>
  .controls {
    background: var(--bg-panel);
    border: 1px solid var(--border-dim);
    border-radius: 4px;
    padding: 14px 16px;
    margin-bottom: 14px;
  }

  .controls .form-row { margin-bottom: 10px; }
  .controls .form-row:last-child { margin-bottom: 0; }

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

  .accent { color: var(--accent); }
  .sep { opacity: 0.4; }

  .hint {
    color: var(--muted);
    font-size: 12px;
    letter-spacing: 0.05em;
    padding: 20px 0;
  }
</style>
