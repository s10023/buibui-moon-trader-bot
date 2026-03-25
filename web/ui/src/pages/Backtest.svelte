<script lang="ts">
  import { onMount } from "svelte";
  import {
    runBacktest,
    getBacktestRuns,
    type BacktestResponse,
    type BacktestRunSummary,
  } from "../api";
  import { symbols } from "../stores/config";
  import { strategiesStore, strategyNames } from "../stores/strategies";
  import BacktestResultCmp from "../components/BacktestResult.svelte";
  import ErrorBanner from "../components/ErrorBanner.svelte";

  const TIMEFRAMES = ["15m", "1h", "4h", "1d"];

  // ── DB runs view ────────────────────────────────────────────────────────────
  let runs = $state<BacktestRunSummary[]>([]);
  let runsLoading = $state(true);
  let runsError = $state<string | null>(null);

  let filterSymbol = $state("");
  let filterTf = $state("");
  let filterStrategy = $state("");
  let sortCol = $state<keyof BacktestRunSummary>("avg_r");
  let sortDir = $state<"asc" | "desc">("desc");

  async function loadRuns(): Promise<void> {
    runsError = null;
    try {
      runs = await getBacktestRuns();
    } catch (e) {
      runsError = e instanceof Error ? e.message : String(e);
    } finally {
      runsLoading = false;
    }
  }

  onMount(() => { void loadRuns(); });

  const filteredRuns = $derived.by(() => {
    const fs = filterSymbol;
    const ft = filterTf;
    const fst = filterStrategy;
    const col = sortCol;
    const dir = sortDir;
    const filtered = runs.filter(
      (r) =>
        (!fs || r.symbol === fs) &&
        (!ft || r.timeframe === ft) &&
        (!fst || r.strategy === fst),
    );
    filtered.sort((a, b) => {
      const av = a[col];
      const bv = b[col];
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return dir === "desc" ? -cmp : cmp;
    });
    return filtered;
  });

  function setSort(col: keyof BacktestRunSummary): void {
    if (sortCol === col) {
      sortDir = sortDir === "desc" ? "asc" : "desc";
    } else {
      sortCol = col;
      sortDir = "desc";
    }
  }

  function sortIcon(col: string): string {
    if (sortCol !== col) return "⇅";
    return sortDir === "desc" ? "↓" : "↑";
  }

  function starsFor(strategy: string): number {
    return $strategiesStore[strategy]?.confidence ?? 0;
  }

  function renderStars(n: number): string {
    return "★".repeat(n) + "☆".repeat(5 - n);
  }

  function fmtDate(ms: number): string {
    return new Intl.DateTimeFormat("en-MY", {
      timeZone: "Asia/Kuala_Lumpur",
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(new Date(ms));
  }

  function fmtR(r: number): string {
    return (r >= 0 ? "+" : "") + r.toFixed(2) + "R";
  }

  function fmtWinPct(w: number): string {
    return (w * 100).toFixed(1) + "%";
  }

  // ── Run form ─────────────────────────────────────────────────────────────────
  let showForm = $state(false);
  let symbol = $state("BTCUSDT");
  let timeframe = $state("4h");
  let strategy = $state("bos");
  let days = $state(200);
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

  // Check if this exact symbol+tf+strategy already has a saved run
  const existingRun = $derived(
    runs.find((r) => r.symbol === symbol && r.timeframe === timeframe && r.strategy === strategy) ?? null,
  );

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
      runsLoading = true;
      await loadRuns();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  const currentSpec = $derived($strategiesStore[strategy]);
</script>

<div class="page">
  <div class="page-header">
    <h2>Backtest</h2>
    <button class="toggle-btn" class:active={showForm} onclick={() => { showForm = !showForm; }}>
      {showForm ? "✕ Close" : "▶ Run Backtest"}
    </button>
  </div>

  <!-- ── DB Results ──────────────────────────────────────────────────────── -->
  <div class="filter-bar">
    <select bind:value={filterSymbol}>
      <option value="">All Symbols</option>
      {#each $symbols as s}<option value={s}>{s}</option>{/each}
    </select>
    <select bind:value={filterTf}>
      <option value="">All TFs</option>
      {#each TIMEFRAMES as tf}<option value={tf}>{tf}</option>{/each}
    </select>
    <select bind:value={filterStrategy}>
      <option value="">All Strategies</option>
      {#each $strategyNames as s}<option value={s}>{s}</option>{/each}
    </select>
    <span class="count">
      {#if runsLoading}
        loading…
      {:else}
        {filteredRuns.length} / {runs.length}
      {/if}
    </span>
  </div>

  {#if runsError}
    <ErrorBanner error={runsError} />
  {/if}

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th class="sortable" onclick={() => setSort("symbol")}>Symbol <span class="sort-icon">{sortIcon("symbol")}</span></th>
          <th class="sortable" onclick={() => setSort("timeframe")}>TF <span class="sort-icon">{sortIcon("timeframe")}</span></th>
          <th class="sortable" onclick={() => setSort("strategy")}>Strategy <span class="sort-icon">{sortIcon("strategy")}</span></th>
          <th>★</th>
          <th class="sortable num-col" onclick={() => setSort("win_rate")}>Win% <span class="sort-icon">{sortIcon("win_rate")}</span></th>
          <th class="sortable num-col" onclick={() => setSort("closed_trades")}>Trades <span class="sort-icon">{sortIcon("closed_trades")}</span></th>
          <th class="sortable num-col" onclick={() => setSort("avg_r")}>Avg R <span class="sort-icon">{sortIcon("avg_r")}</span></th>
          <th class="sortable num-col" onclick={() => setSort("total_r")}>Total R <span class="sort-icon">{sortIcon("total_r")}</span></th>
          <th>Day Filter</th>
          <th class="sortable" onclick={() => setSort("run_at_ms")}>Date <span class="sort-icon">{sortIcon("run_at_ms")}</span></th>
        </tr>
      </thead>
      <tbody>
        {#if runsLoading}
          <tr><td colspan="10" class="loading-cell">Loading…</td></tr>
        {:else if filteredRuns.length === 0}
          <tr><td colspan="10" class="empty-cell">No results — run a backtest first or adjust filters.</td></tr>
        {:else}
          {#each filteredRuns as run (run.run_id)}
            <tr>
              <td class="sym">{run.symbol.replace("USDT", "")}</td>
              <td class="muted">{run.timeframe}</td>
              <td class="strategy-name">{run.strategy}</td>
              <td class="stars">{renderStars(starsFor(run.strategy))}</td>
              <td class="num">{fmtWinPct(run.win_rate)}</td>
              <td class="num muted">{run.closed_trades}</td>
              <td class="num" class:pos={run.avg_r > 0} class:neg={run.avg_r < 0}>{fmtR(run.avg_r)}</td>
              <td class="num" class:pos={run.total_r > 0} class:neg={run.total_r < 0}>{fmtR(run.total_r)}</td>
              <td class="muted day-filter">{run.day_filter}</td>
              <td class="muted date">{fmtDate(run.run_at_ms)}</td>
            </tr>
          {/each}
        {/if}
      </tbody>
    </table>
  </div>

  <!-- ── Run Form ────────────────────────────────────────────────────────── -->
  {#if showForm}
    <div class="run-section">
      <div class="run-header">Run New Backtest</div>

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
            {loading ? "Running…" : existingRun ? "↺ Re-run & Update" : "▶ Run Backtest"}
          </button>
          {#if existingRun}
            <span class="update-hint">Will update existing run from {fmtDate(existingRun.run_at_ms)}</span>
          {/if}
          {#if currentSpec}
            <span class="strat-desc">{currentSpec.description}</span>
          {/if}
        </div>
      </div>

      {#if result}
        <BacktestResultCmp {result} />
      {/if}
    </div>
  {/if}
</div>

<style>
  .page-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid var(--border-dim);
    margin-bottom: 16px;
  }

  .page-header h2 {
    border-bottom: none;
    margin-bottom: 0;
  }

  .toggle-btn {
    font-size: 10px;
    padding: 4px 12px;
  }

  .toggle-btn.active {
    background: var(--accent-dim);
    color: var(--accent-bright);
  }

  /* ── Filters ──────────────────────────────────────────────────────────── */
  .filter-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
    flex-wrap: wrap;
  }

  .filter-bar select {
    font-size: 11px;
    padding: 4px 7px;
  }

  .count {
    margin-left: auto;
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 0.06em;
  }

  /* ── Table ────────────────────────────────────────────────────────────── */
  .table-wrap {
    overflow-x: auto;
    margin-bottom: 4px;
  }

  th.sortable {
    cursor: pointer;
    user-select: none;
  }

  th.sortable:hover {
    color: var(--text-dim);
  }

  th.num-col {
    text-align: right;
  }

  .sort-icon {
    font-size: 9px;
    color: var(--muted);
    margin-left: 2px;
  }

  .sym {
    font-weight: 600;
    color: var(--text);
  }

  .strategy-name {
    color: var(--text-dim);
    font-size: 11.5px;
  }

  .stars {
    color: var(--yellow);
    font-size: 10px;
    letter-spacing: -1px;
    white-space: nowrap;
  }

  td.num {
    text-align: right;
    font-feature-settings: "tnum" 1;
  }

  td.pos { color: var(--green); }
  td.neg { color: var(--red); }

  td.day-filter {
    font-size: 11px;
  }

  td.date {
    font-size: 11px;
    white-space: nowrap;
  }

  .loading-cell,
  .empty-cell {
    text-align: center;
    color: var(--muted);
    padding: 24px;
    font-size: 11px;
  }

  /* ── Run section ──────────────────────────────────────────────────────── */
  .run-section {
    margin-top: 20px;
    border-top: 1px solid var(--border-dim);
    padding-top: 16px;
  }

  .run-header {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 12px;
  }

  .run-header::before {
    content: "//  ";
    color: var(--accent);
  }

  .form-section {
    background: var(--bg-panel);
    border: 1px solid var(--border-dim);
    border-radius: 4px;
    padding: 16px;
    margin-bottom: 4px;
  }

  .update-hint {
    font-size: 10px;
    color: var(--yellow);
    padding-left: 4px;
  }

  .strat-desc {
    font-size: 11px;
    color: var(--muted);
    padding-left: 4px;
    font-style: italic;
  }
</style>
