<script lang="ts">
  import { runBacktest, type BacktestResponse } from "../api";
  import { symbols } from "../stores/config";
  import { strategiesStore, strategyNames } from "../stores/strategies";
  import BacktestResultCmp from "../components/BacktestResult.svelte";
  import ErrorBanner from "../components/ErrorBanner.svelte";

  const TIMEFRAMES = ["15m", "1h", "4h", "1d"];

  let symbol = $state("BTCUSDT");
  let timeframe = $state("4h");
  let strategy = $state("fvg");
  let days = $state(90);
  let sl_pct = $state(2.0);
  let tp_r = $state(2.0);
  let fee_pct = $state(0.05);
  let extraParams = $state<Record<string, number>>({});
  let result = $state<BacktestResponse | null>(null);
  let loading = $state(false);
  let error = $state<string | null>(null);

  $effect(() => {
    const spec = $strategiesStore[strategy];
    if (!spec) return;
    extraParams = Object.fromEntries(spec.params.map((p) => [p.name, p.default]));
  });

  async function submit(): Promise<void> {
    loading = true;
    error = null;
    result = null;
    try {
      result = await runBacktest({
        symbol,
        timeframe,
        strategy,
        days,
        sl_pct: sl_pct / 100,
        tp_r,
        fee_pct: fee_pct / 100,
        ...extraParams,
      });
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  const currentSpec = $derived($strategiesStore[strategy]);
</script>

<div class="page">
  <h2>Backtest</h2>

  {#if error}<ErrorBanner {error} />{/if}

  <div class="form-section">
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
      <label>Strategy
        <select bind:value={strategy}>
          {#each $strategyNames as s}<option>{s}</option>{/each}
        </select>
      </label>
    </div>

    <div class="form-row">
      <label>Days <input type="number" bind:value={days} min="7" max="365" /></label>
      <label title="Stop-loss distance from entry, e.g. 2 = 2%">SL % <input type="number" bind:value={sl_pct} min="0.5" max="10" step="0.5" /></label>
      <label title="Take-profit as a multiple of the SL distance, e.g. 2 = 2R">TP (R) <input type="number" bind:value={tp_r} min="0.5" max="10" step="0.5" /></label>
      <label title="Taker fee per side, e.g. 0.05 = 0.05% per trade">Fee % <input type="number" bind:value={fee_pct} min="0" max="0.5" step="0.01" /></label>
    </div>

    {#if currentSpec && currentSpec.params.length > 0}
      <div class="form-row">
        {#each currentSpec.params as p}
          <label>{p.name}
            <input
              type="number"
              bind:value={extraParams[p.name]}
              min={p.min_val}
              max={p.max_val}
              step={p.param_type === "int" ? 1 : 0.01}
              title={p.description}
            />
          </label>
        {/each}
      </div>
    {/if}

    <div class="form-row">
      <button disabled={loading} onclick={() => void submit()}>
        {loading ? "Running..." : "Run Backtest"}
      </button>
      {#if currentSpec}
        <span class="strat-desc">{currentSpec.description}</span>
      {/if}
    </div>
  </div>

  {#if result}
    <BacktestResultCmp {result} />
  {/if}
</div>

<style>
  .form-section {
    background: var(--bg-panel);
    border: 1px solid var(--border-dim);
    border-radius: 4px;
    padding: 16px;
    margin-bottom: 4px;
  }

  .strat-desc {
    font-size: 11px;
    color: var(--muted);
    padding-left: 4px;
    font-style: italic;
  }
</style>
