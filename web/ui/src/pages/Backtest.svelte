<script lang="ts">
  import { onMount } from "svelte";
  import {
    runBacktest,
    getBacktestRuns,
    getStrategies,
    getConfidenceConfigs,
    type BacktestResponse,
    type BacktestRunSummary,
    type StrategiesResponse,
  } from "../api";
  import { symbols } from "../stores/config";
  import { strategiesStore, strategyNames } from "../stores/strategies";
  import BacktestResultCmp from "../components/BacktestResult.svelte";
  import ErrorBanner from "../components/ErrorBanner.svelte";

  const TIMEFRAMES = ["15m", "1h", "4h", "1d"];

  // ── Per-config star ratings ───────────────────────────────────────────────────
  let confidenceConfigs = $state<string[]>([]);
  let selectedConfig = $state<string | null>(null);
  let configStars = $state<StrategiesResponse>({});

  async function loadConfidenceConfigs(): Promise<void> {
    try {
      confidenceConfigs = await getConfidenceConfigs();
    } catch {
      confidenceConfigs = [];
    }
  }

  async function selectConfig(name: string | null): Promise<void> {
    selectedConfig = name;
    if (!name) {
      configStars = {};
      return;
    }
    try {
      configStars = await getStrategies(name);
    } catch {
      configStars = {};
    }
  }

  // ── DB runs view ─────────────────────────────────────────────────────────────
  let runs = $state<BacktestRunSummary[]>([]);
  let runsLoading = $state(true);
  let runsError = $state<string | null>(null);

  // Multi-select filters — empty Set = no filter (all pass)
  let selSymbols = $state(new Set<string>());
  let selTfs = $state(new Set<string>());
  let selStrategies = $state(new Set<string>());
  let selDayFilters = $state(new Set<string>());
  let minStarsFilter = $state(0);         // 0 = any
  let minTrades = $state(0);              // 0 = no minimum
  let minAvgRText = $state("");           // "" = no minimum
  let minLongWinRateText = $state("");    // "" = no minimum
  let minShortWinRateText = $state("");   // "" = no minimum
  let minLongAvgRText = $state("");       // "" = no minimum
  let minShortAvgRText = $state("");      // "" = no minimum
  let minWinRateText = $state("");        // "" = no minimum
  let minTotalRText = $state("");         // "" = no minimum
  let maxDrawdownRText = $state("");      // "" = no maximum

  type SortCol = keyof BacktestRunSummary | "stars";
  let sortCol = $state<SortCol>("avg_r");
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

  onMount(() => { void loadRuns(); void loadConfidenceConfigs(); });

  const availableDayFilters = $derived(
    [...new Set(runs.map((r) => r.day_filter))].sort(),
  );

  const hasActiveFilters = $derived(
    selSymbols.size > 0 ||
    selTfs.size > 0 ||
    selStrategies.size > 0 ||
    selDayFilters.size > 0 ||
    minStarsFilter > 0 ||
    minTrades > 0 ||
    minAvgRText !== "" ||
    minLongWinRateText !== "" ||
    minShortWinRateText !== "" ||
    minLongAvgRText !== "" ||
    minShortAvgRText !== "" ||
    minWinRateText !== "" ||
    minTotalRText !== "" ||
    maxDrawdownRText !== "",
  );

  const filteredRuns = $derived.by(() => {
    const col = sortCol;
    const dir = sortDir;
    const mss = minStarsFilter;
    const mt = minTrades;
    const mar   = minAvgRText        === "" ? -Infinity : (parseFloat(minAvgRText)        || -Infinity);
    const mlwr  = minLongWinRateText  === "" ? -Infinity : (parseFloat(minLongWinRateText)  / 100 || -Infinity);
    const mswr  = minShortWinRateText === "" ? -Infinity : (parseFloat(minShortWinRateText) / 100 || -Infinity);
    const mlar  = minLongAvgRText     === "" ? -Infinity : (parseFloat(minLongAvgRText)      || -Infinity);
    const msar  = minShortAvgRText    === "" ? -Infinity : (parseFloat(minShortAvgRText)     || -Infinity);
    const mwr   = minWinRateText      === "" ? -Infinity : (parseFloat(minWinRateText)  / 100 || -Infinity);
    const mtr   = minTotalRText       === "" ? -Infinity : (parseFloat(minTotalRText)        || -Infinity);
    // user types negative (e.g. -10); stored value is positive — negate to compare
    const mxdd  = maxDrawdownRText    === "" ? Infinity  : (-parseFloat(maxDrawdownRText)    || Infinity);

    const filtered = runs.filter(
      (r) =>
        (selSymbols.size === 0 || selSymbols.has(r.symbol)) &&
        (selTfs.size === 0 || selTfs.has(r.timeframe)) &&
        (selStrategies.size === 0 || selStrategies.has(r.strategy)) &&
        (selDayFilters.size === 0 || selDayFilters.has(r.day_filter)) &&
        (mss === 0 || starsFor(r.strategy, r.timeframe) >= mss) &&
        r.closed_trades >= mt &&
        r.avg_r >= mar &&
        r.win_rate >= mwr &&
        r.total_r >= mtr &&
        r.max_drawdown_r <= mxdd &&
        (mlwr === -Infinity || (r.long_win_rate !== null && r.long_win_rate >= mlwr)) &&
        (mswr === -Infinity || (r.short_win_rate !== null && r.short_win_rate >= mswr)) &&
        (mlar === -Infinity || (r.long_avg_r !== null && r.long_avg_r >= mlar)) &&
        (msar === -Infinity || (r.short_avg_r !== null && r.short_avg_r >= msar)),
    );

    filtered.sort((a, b) => {
      const av: number | string =
        col === "stars"
          ? starsFor(a.strategy)
          : (a[col as keyof BacktestRunSummary] as number | string | null) ?? "";
      const bv: number | string =
        col === "stars"
          ? starsFor(b.strategy)
          : (b[col as keyof BacktestRunSummary] as number | string | null) ?? "";
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return dir === "desc" ? -cmp : cmp;
    });
    return filtered;
  });

  // Svelte 5: must reassign $state variable — in-place Set mutation is not tracked
  function tog(current: Set<string>, val: string): Set<string> {
    const next = new Set(current);
    if (next.has(val)) next.delete(val);
    else next.add(val);
    return next;
  }

  function resetFilters(): void {
    selSymbols = new Set();
    selTfs = new Set();
    selStrategies = new Set();
    selDayFilters = new Set();
    minStarsFilter = 0;
    minTrades = 0;
    minAvgRText = "";
    minLongWinRateText = "";
    minShortWinRateText = "";
    minLongAvgRText = "";
    minShortAvgRText = "";
    minWinRateText = "";
    minTotalRText = "";
    maxDrawdownRText = "";
  }

  function setSort(col: SortCol): void {
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

  function starsFor(strategy: string, tf?: string): number {
    // Use per-config DB stars if a config is selected, fall back to global registry
    const source = selectedConfig ? configStars : $strategiesStore;
    const conf = source[strategy]?.confidence;
    if (conf === undefined || conf === null) return 0;
    if (typeof conf === "number") return conf;
    // dict[str, int] — resolve per-TF with "default" fallback (mirrors Python get_confidence)
    if (tf && tf in conf) return conf[tf];
    return conf["default"] ?? 3;
  }

  function renderStars(n: number): string {
    return "★".repeat(n) + "☆".repeat(5 - n);
  }

  function configLabel(name: string): string {
    return name.replace(/^signal_watch_?/, "") || "default";
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

  function fmtDirWinPct(w: number | null): string {
    return w === null ? "—" : (w * 100).toFixed(1) + "%";
  }

  function fmtDirR(r: number | null): string {
    return r === null ? "—" : (r >= 0 ? "+" : "") + r.toFixed(2) + "R";
  }

  // ── Run form ──────────────────────────────────────────────────────────────────
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

  const existingRun = $derived(
    runs.find(
      (r) => r.symbol === symbol && r.timeframe === timeframe && r.strategy === strategy,
    ) ?? null,
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

  <!-- ── Filters ──────────────────────────────────────────────────────────── -->
  <div class="filters">

    <div class="filter-row">
      <span class="flabel">Symbol</span>
      <div class="chips">
        {#each $symbols as s}
          <button class="chip" class:on={selSymbols.has(s)}
            onclick={() => { selSymbols = tog(selSymbols, s); }}>{s.replace("USDT", "")}</button>
        {/each}
      </div>

      <span class="fsep"></span>

      <span class="flabel">TF</span>
      <div class="chips">
        {#each TIMEFRAMES as tf}
          <button class="chip" class:on={selTfs.has(tf)}
            onclick={() => { selTfs = tog(selTfs, tf); }}>{tf}</button>
        {/each}
      </div>

      <span class="fsep"></span>

      <span class="flabel">Day Filter</span>
      <div class="chips">
        {#each availableDayFilters as df}
          <button class="chip" class:on={selDayFilters.has(df)}
            onclick={() => { selDayFilters = tog(selDayFilters, df); }}>{df}</button>
        {/each}
      </div>

      <span class="fsep"></span>

      <span class="flabel">Stars ≥</span>
      <div class="chips">
        <button class="chip" class:on={minStarsFilter === 0}
          onclick={() => { minStarsFilter = 0; }}>any</button>
        {#each [1, 2, 3, 4, 5] as n}
          <button class="chip star-chip" class:on={minStarsFilter === n}
            onclick={() => { minStarsFilter = n; }}>{"★".repeat(n)}</button>
        {/each}
      </div>

      {#if confidenceConfigs.length > 0}
        <span class="fsep"></span>

        <span class="flabel">Stars from</span>
        <div class="chips">
          <button class="chip" class:on={selectedConfig === null}
            onclick={() => { void selectConfig(null); }}>default</button>
          {#each confidenceConfigs as cfg}
            <button class="chip" class:on={selectedConfig === cfg}
              onclick={() => { void selectConfig(cfg); }}>{configLabel(cfg)}</button>
          {/each}
        </div>
      {/if}
    </div>

    <div class="filter-row">
      <span class="flabel">Strategy</span>
      <details class="strat-picker">
        <summary class="strat-summary">
          {selStrategies.size === 0 ? "all" : `${selStrategies.size} selected`}
          <span class="summary-arrow">▾</span>
        </summary>
        <div class="strat-grid">
          {#each $strategyNames as s}
            <button class="chip" class:on={selStrategies.has(s)}
              onclick={() => { selStrategies = tog(selStrategies, s); }}>{s}</button>
          {/each}
        </div>
      </details>

      <span class="fsep"></span>

      <span class="flabel">Win% ≥</span>
      <input
        type="number"
        class="filter-num"
        bind:value={minWinRateText}
        min="0"
        max="100"
        step="1"
        placeholder="any"
        title="Minimum win rate % (empty = no minimum)"
      />

      <span class="fsep"></span>

      <span class="flabel">Trades ≥</span>
      <input
        type="number"
        class="filter-num"
        bind:value={minTrades}
        min="0"
        step="1"
        title="Minimum closed trades (0 = no minimum)"
      />

      <span class="fsep"></span>

      <span class="flabel">Avg R ≥</span>
      <input
        type="number"
        class="filter-num"
        bind:value={minAvgRText}
        step="0.01"
        placeholder="any"
        title="Minimum avg R (empty = no minimum)"
      />

      <span class="fsep"></span>

      <span class="flabel">Total R ≥</span>
      <input
        type="number"
        class="filter-num"
        bind:value={minTotalRText}
        step="0.1"
        placeholder="any"
        title="Minimum total R (empty = no minimum)"
      />

      <span class="fsep"></span>

      <span class="flabel">Max DD ≤</span>
      <input
        type="number"
        class="filter-num"
        bind:value={maxDrawdownRText}
        max="0"
        step="0.5"
        placeholder="any"
        title="Maximum drawdown, e.g. -10 = no worse than -10R (empty = no limit)"
      />

      <span class="fsep"></span>

      <span class="flabel long-label">↑ Long ≥</span>
      <input
        type="number"
        class="filter-num"
        bind:value={minLongWinRateText}
        min="0"
        max="100"
        step="1"
        placeholder="any"
        title="Minimum long win rate % (empty = no minimum)"
      />

      <span class="flabel short-label">↓ Short ≥</span>
      <input
        type="number"
        class="filter-num"
        bind:value={minShortWinRateText}
        min="0"
        max="100"
        step="1"
        placeholder="any"
        title="Minimum short win rate % (empty = no minimum)"
      />

      <span class="fsep"></span>

      <span class="flabel long-label">↑ L Avg R ≥</span>
      <input
        type="number"
        class="filter-num"
        bind:value={minLongAvgRText}
        step="0.01"
        placeholder="any"
        title="Minimum long avg R (empty = no minimum)"
      />

      <span class="flabel short-label">↓ S Avg R ≥</span>
      <input
        type="number"
        class="filter-num"
        bind:value={minShortAvgRText}
        step="0.01"
        placeholder="any"
        title="Minimum short avg R (empty = no minimum)"
      />

      <div class="filter-tail">
        {#if hasActiveFilters}
          <button class="reset-btn" onclick={resetFilters}>✕ Reset</button>
        {/if}
        <span class="count">
          {#if runsLoading}loading…{:else}{filteredRuns.length} / {runs.length}{/if}
        </span>
      </div>
    </div>

  </div>

  {#if runsError}
    <ErrorBanner error={runsError} />
  {/if}

  <!-- ── Results Table ─────────────────────────────────────────────────── -->
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th class="sortable" onclick={() => setSort("symbol")}>Symbol <span class="si">{sortIcon("symbol")}</span></th>
          <th class="sortable" onclick={() => setSort("timeframe")}>TF <span class="si">{sortIcon("timeframe")}</span></th>
          <th class="sortable" onclick={() => setSort("strategy")}>Strategy <span class="si">{sortIcon("strategy")}</span></th>
          <th class="sortable" onclick={() => setSort("stars")}>★ <span class="si">{sortIcon("stars")}</span></th>
          <th class="sortable num-col" onclick={() => setSort("win_rate")}>Win% <span class="si">{sortIcon("win_rate")}</span></th>
          <th class="sortable num-col long-col" onclick={() => setSort("long_win_rate")}>↑ Long% <span class="si">{sortIcon("long_win_rate")}</span></th>
          <th class="sortable num-col long-col" onclick={() => setSort("long_avg_r")}>↑ L Avg R <span class="si">{sortIcon("long_avg_r")}</span></th>
          <th class="sortable num-col short-col" onclick={() => setSort("short_win_rate")}>↓ Short% <span class="si">{sortIcon("short_win_rate")}</span></th>
          <th class="sortable num-col short-col" onclick={() => setSort("short_avg_r")}>↓ S Avg R <span class="si">{sortIcon("short_avg_r")}</span></th>
          <th class="sortable num-col" onclick={() => setSort("closed_trades")}>Trades <span class="si">{sortIcon("closed_trades")}</span></th>
          <th class="sortable num-col" onclick={() => setSort("avg_r")}>Avg R <span class="si">{sortIcon("avg_r")}</span></th>
          <th class="sortable num-col" onclick={() => setSort("total_r")}>Total R <span class="si">{sortIcon("total_r")}</span></th>
          <th class="sortable num-col dd-col" onclick={() => setSort("max_drawdown_r")}>Max DD <span class="si">{sortIcon("max_drawdown_r")}</span></th>
          <th class="sortable" onclick={() => setSort("day_filter")}>Day Filter <span class="si">{sortIcon("day_filter")}</span></th>
          <th class="sortable" onclick={() => setSort("run_at_ms")}>Date <span class="si">{sortIcon("run_at_ms")}</span></th>
        </tr>
      </thead>
      <tbody>
        {#if runsLoading}
          <tr><td colspan="15" class="msg-cell">Loading…</td></tr>
        {:else if filteredRuns.length === 0}
          <tr>
            <td colspan="15" class="msg-cell">
              {hasActiveFilters ? "No results match current filters." : "No runs saved — run a backtest first."}
            </td>
          </tr>
        {:else}
          {#each filteredRuns as run (run.run_id)}
            <tr>
              <td class="sym">{run.symbol.replace("USDT", "")}</td>
              <td class="muted">{run.timeframe}</td>
              <td class="strat-name">{run.strategy}</td>
              <td class="stars">{renderStars(starsFor(run.strategy, run.timeframe))}</td>
              <td class="num">{fmtWinPct(run.win_rate)}</td>
              <td class="num dir-long" class:dir-pos={run.long_win_rate !== null && run.long_win_rate > 0.5} class:dir-nil={run.long_win_rate === null}>{fmtDirWinPct(run.long_win_rate)}</td>
              <td class="num dir-long" class:pos={run.long_avg_r !== null && run.long_avg_r > 0} class:neg={run.long_avg_r !== null && run.long_avg_r < 0} class:dir-nil={run.long_avg_r === null}>{fmtDirR(run.long_avg_r)}</td>
              <td class="num dir-short" class:dir-pos={run.short_win_rate !== null && run.short_win_rate > 0.5} class:dir-nil={run.short_win_rate === null}>{fmtDirWinPct(run.short_win_rate)}</td>
              <td class="num dir-short" class:pos={run.short_avg_r !== null && run.short_avg_r > 0} class:neg={run.short_avg_r !== null && run.short_avg_r < 0} class:dir-nil={run.short_avg_r === null}>{fmtDirR(run.short_avg_r)}</td>
              <td class="num muted">{run.closed_trades}</td>
              <td class="num" class:pos={run.avg_r > 0} class:neg={run.avg_r < 0}>{fmtR(run.avg_r)}</td>
              <td class="num" class:pos={run.total_r > 0} class:neg={run.total_r < 0}>{fmtR(run.total_r)}</td>
              <td class="num dd-val" class:dd-bad={run.max_drawdown_r > 10}>-{run.max_drawdown_r.toFixed(1)}R</td>
              <td class="muted small">{run.day_filter}</td>
              <td class="muted small nowrap">{fmtDate(run.run_at_ms)}</td>
            </tr>
          {/each}
        {/if}
      </tbody>
    </table>
  </div>

  <!-- ── Run Form ──────────────────────────────────────────────────────── -->
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
          <label title="Take-profit as a multiple of the SL distance">TP (R) <input type="number" bind:value={tp_r} min="0.5" max="10" step="0.5" /></label>
          <label title="Taker fee per side, e.g. 0.05 = 0.05%">Fee % <input type="number" bind:value={fee_pct} min="0" max="0.5" step="0.01" /></label>
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
  /* ── Page header ─────────────────────────────────────────────────────── */
  .page-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid var(--border-dim);
    margin-bottom: 14px;
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

  /* ── Filters ─────────────────────────────────────────────────────────── */
  .filters {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-bottom: 12px;
  }

  .filter-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
  }

  .flabel {
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
    white-space: nowrap;
    flex-shrink: 0;
  }

  .fsep {
    width: 1px;
    height: 14px;
    background: var(--border);
    flex-shrink: 0;
    margin: 0 2px;
  }

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .chip {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.04em;
    padding: 2px 7px;
    border: 1px solid var(--border);
    border-radius: 2px;
    background: transparent;
    color: var(--text-dim);
    cursor: pointer;
    transition: background 80ms, color 80ms, border-color 80ms;
  }

  .chip:hover:not(.on) {
    border-color: var(--border-dim);
    color: var(--text);
    background: var(--bg-panel);
  }

  .chip.on {
    background: var(--accent-dim);
    border-color: var(--accent);
    color: var(--accent-bright);
  }

  .star-chip.on {
    color: var(--yellow);
    border-color: var(--yellow);
    background: rgba(240, 192, 64, 0.1);
  }

  /* ── Strategy picker ─────────────────────────────────────────────────── */
  .strat-picker {
    position: relative;
  }

  .strat-summary {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.04em;
    padding: 2px 7px;
    border: 1px solid var(--border);
    border-radius: 2px;
    background: transparent;
    color: var(--text-dim);
    cursor: pointer;
    list-style: none;
    display: flex;
    align-items: center;
    gap: 4px;
    transition: background 80ms, border-color 80ms;
    user-select: none;
  }

  .strat-summary::-webkit-details-marker { display: none; }

  .strat-picker[open] .strat-summary {
    border-color: var(--accent);
    color: var(--accent-bright);
    background: var(--accent-dim);
  }

  .summary-arrow {
    font-size: 8px;
    color: var(--muted);
  }

  .strat-grid {
    position: absolute;
    top: calc(100% + 4px);
    left: 0;
    z-index: 10;
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 8px;
    display: grid;
    grid-template-columns: repeat(4, auto);
    gap: 4px;
    min-width: 360px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
  }

  /* ── Numeric filters ─────────────────────────────────────────────────── */
  .filter-num {
    width: 70px;
    font-size: 11px;
    padding: 3px 6px;
  }

  .filter-tail {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-left: auto;
  }

  .reset-btn {
    font-size: 9px;
    padding: 2px 8px;
    color: var(--red);
    border-color: var(--red);
  }

  .reset-btn:hover:not(:disabled) {
    background: var(--red-dim);
    color: var(--red);
  }

  .count {
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 0.06em;
    white-space: nowrap;
  }

  /* ── Table ───────────────────────────────────────────────────────────── */
  .table-wrap {
    overflow-x: auto;
    margin-bottom: 4px;
  }

  th.sortable {
    cursor: pointer;
    user-select: none;
  }

  th.sortable:hover { color: var(--text-dim); }

  th.num-col { text-align: right; }

  .si {
    font-size: 9px;
    color: var(--muted);
    margin-left: 2px;
  }

  .sym { font-weight: 600; }

  .strat-name {
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

  td.small { font-size: 11px; }
  td.nowrap { white-space: nowrap; }

  .msg-cell {
    text-align: center;
    color: var(--muted);
    padding: 24px;
    font-size: 11px;
  }

  /* ── Run section ─────────────────────────────────────────────────────── */
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

  /* ── Direction split columns ──────────────────────────────────────────── */
  .long-label { color: var(--green); }
  .short-label { color: var(--red); }

  th.long-col  { color: var(--green); opacity: 0.75; }
  th.short-col { color: var(--red);   opacity: 0.75; }

  td.dir-long,
  td.dir-short {
    color: var(--muted);
    font-feature-settings: "tnum" 1;
  }

  td.dir-long.dir-pos  { color: var(--green); }
  td.dir-short.dir-pos { color: var(--green); }
  td.dir-nil           { color: var(--border); letter-spacing: 0.05em; }

  /* ── Max DD column ─────────────────────────────────────────────────────── */
  th.dd-col  { color: var(--red); opacity: 0.65; }
  td.dd-val  { color: var(--muted); font-feature-settings: "tnum" 1; }
  td.dd-bad  { color: var(--red); }
</style>
