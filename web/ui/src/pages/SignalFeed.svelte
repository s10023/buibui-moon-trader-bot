<script lang="ts">
  import { onMount } from "svelte";
  import { get } from "svelte/store";
  import { getSignals } from "../api";
  import { symbols } from "../stores/config";
  import { strategyNames } from "../stores/strategies";
  import {
    signalsStore,
    signalsLoading,
    signalsError,
    signalsLastRefresh,
    type SignalWithMeta,
  } from "../stores/signals";
  import ErrorBanner from "../components/ErrorBanner.svelte";
  import LoadingSpinner from "../components/LoadingSpinner.svelte";

  const TIMEFRAMES = ["15m", "1h", "4h", "1d"];
  const POLL_MS = 60_000;

  let filterSymbol = $state("");
  let filterStrategy = $state("");
  let filterDirection = $state<"" | "long" | "short">("");

  const nowMs = () => Date.now();
  const start90d = () => nowMs() - 90 * 24 * 60 * 60 * 1000;

  async function fetchAll(): Promise<void> {
    signalsError.set(null);
    try {
      const allSymbols = get(symbols);
      const allStrategies = get(strategyNames);
      if (allSymbols.length === 0 || allStrategies.length === 0) return;

      const settled = await Promise.allSettled(
        allSymbols.flatMap((sym) =>
          TIMEFRAMES.map(async (tf) => {
            const resp = await getSignals({
              symbol: sym,
              timeframe: tf,
              start_ms: start90d(),
              end_ms: nowMs(),
              strategies: allStrategies,
            });
            return resp.signals.map((s): SignalWithMeta => ({ ...s, symbol: sym, timeframe: tf }));
          })
        )
      );
      // 404 = no OHLCV data for that symbol/tf — skip silently
      signalsStore.set(
        settled
          .flatMap((r) => (r.status === "fulfilled" ? r.value : []))
          .sort((a, b) => b.open_time - a.open_time)
          .slice(0, 100)
      );
      signalsLastRefresh.set(new Date());
    } catch (e) {
      signalsError.set(String(e));
    } finally {
      signalsLoading.set(false);
    }
  }

  let interval: ReturnType<typeof setInterval>;
  onMount(() => {
    // Delay initial fetch until config + strategies are loaded (they're async in App.svelte).
    // Subscribe to symbols; fire fetchAll() as soon as symbols populate (if not already loaded).
    const unsub = symbols.subscribe((syms) => {
      if (syms.length > 0 && get(signalsLoading)) void fetchAll();
    });
    interval = setInterval(() => void fetchAll(), POLL_MS);
    return () => {
      unsub();
      clearInterval(interval);
    };
  });

  const filtered = $derived(
    $signalsStore.filter((s) => {
      if (filterSymbol && s.symbol !== filterSymbol) return false;
      if (filterStrategy && s.strategy !== filterStrategy) return false;
      if (filterDirection && s.direction !== filterDirection) return false;
      return true;
    })
  );

  const fmtTime = (ms: number) =>
    new Date(ms).toLocaleString("en-MY", {
      timeZone: "Asia/Kuala_Lumpur",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });

  const stars = (n: number) => "★".repeat(n) + "☆".repeat(5 - n);

  const refreshTime = $derived(
    $signalsLastRefresh
      ? $signalsLastRefresh.toLocaleTimeString("en-MY", { timeZone: "Asia/Kuala_Lumpur", hour12: false })
      : null
  );
</script>

<div class="page">
  <div class="page-header">
    <h2>Signal Feed</h2>
    {#if refreshTime}
      <span class="refresh-ts">updated {refreshTime} MYT</span>
    {/if}
  </div>

  {#if $signalsError}<ErrorBanner error={$signalsError} />{/if}

  <div class="filter-bar">
    <label>Symbol
      <select bind:value={filterSymbol}>
        <option value="">All</option>
        {#each $symbols as sym}<option>{sym}</option>{/each}
      </select>
    </label>
    <label>Strategy
      <select bind:value={filterStrategy}>
        <option value="">All</option>
        {#each $strategyNames as s}<option>{s}</option>{/each}
      </select>
    </label>
    <label>Direction
      <select bind:value={filterDirection}>
        <option value="">All</option>
        <option value="long">Long</option>
        <option value="short">Short</option>
      </select>
    </label>
    <button onclick={() => void fetchAll()}>Refresh</button>
    <span class="result-count">
      {#if !$signalsLoading}{filtered.length} result{filtered.length !== 1 ? "s" : ""}{/if}
    </span>
  </div>

  {#if $signalsLoading}
    <LoadingSpinner label="Scanning signals..." />
  {:else}
    <table>
      <thead>
        <tr>
          <th>Time (MYT)</th><th>Symbol</th><th>TF</th><th>Strategy</th>
          <th>Dir</th><th>Conf</th><th>SL</th><th>Reason</th>
        </tr>
      </thead>
      <tbody>
        {#each filtered as sig (`${sig.symbol}-${sig.timeframe}-${sig.strategy}-${sig.open_time}`)}
          <tr>
            <td class="mono muted">{fmtTime(sig.open_time)}</td>
            <td class="sym">{sig.symbol}</td>
            <td class="muted">{sig.timeframe}</td>
            <td>{sig.strategy}</td>
            <td class={sig.direction === "long" ? "green" : "red"}>
              {sig.direction.toUpperCase()}
            </td>
            <td class="stars">{stars(sig.confidence)}</td>
            <td class="mono">{sig.sl_price > 0 ? sig.sl_price.toLocaleString() : "—"}</td>
            <td class="reason muted">{sig.reason}</td>
          </tr>
        {/each}
      </tbody>
    </table>

    {#if filtered.length === 0}
      <p class="empty muted">// no signals match current filters</p>
    {/if}
  {/if}
</div>

<style>
  .page-header {
    display: flex;
    align-items: baseline;
    gap: 12px;
  }

  .refresh-ts {
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 0.05em;
    padding-bottom: 14px;
  }

  .filter-bar {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    align-items: flex-end;
    background: var(--bg-panel);
    border: 1px solid var(--border-dim);
    border-radius: 4px;
    padding: 12px 16px;
    margin-bottom: 14px;
  }

  .result-count {
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    align-self: center;
    margin-left: 4px;
  }

  .sym { font-weight: 500; }
  .stars { color: var(--yellow); letter-spacing: -1px; }
  .reason { font-size: 11px; max-width: 200px; }
  .empty { padding: 20px 0; font-size: 12px; letter-spacing: 0.05em; }
</style>
