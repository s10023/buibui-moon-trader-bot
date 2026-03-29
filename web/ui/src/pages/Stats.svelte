<script lang="ts">
  import { onMount } from "svelte";
  import {
    getStats,
    type StatsResponse,
  } from "../api";
  import { symbols } from "../stores/config";
  import LoadingSpinner from "../components/LoadingSpinner.svelte";
  import ErrorBanner from "../components/ErrorBanner.svelte";

  const TIMEFRAMES_DAYS = [30, 90, 180, 365];

  let symbol = $state("BTCUSDT");
  let days = $state(180);
  let stats = $state<StatsResponse | null>(null);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let openHelp = $state<string | null>(null);

  const CARD_HELP: Record<string, { what: string; value: string; example: string }> = {
    p1p2: {
      what: "For each historical day, was the daily LOW or HIGH made first? P1 = whichever came first, P2 = second. Shows overall % and a per-day-of-week breakdown.",
      value: "Before entering a long on a dip, check if today's DOW historically makes the low first. If P1=Low is 62% on Thursdays, the day structure is behind you. If you're shorting a Thursday rally, you're going against historical bias — worth knowing.",
      example: "Thursday: P1=Low 62% → the low typically forms first, then price reverses higher.",
    },
    adr: {
      what: "14-day and 30-day rolling average of (daily high − daily low) / open. Plus today's current range as a % of that average.",
      value: "If today's range is already 95% consumed, the move is likely done — don't chase entries. If it's only 20% consumed, there's plenty of room to run. One of the most actionable stats for deciding whether to enter or sit on hands.",
      example: "ADR(14) = 2.8%, today range = 1.2% → 43% used. Room left to trade.",
    },
    hourly: {
      what: "A 24-column chart showing what fraction of days each MYT hour produced the daily high (green) vs daily low (red). Peaks are labelled.",
      value: "This is your empirically-derived kill zone — not fixed ICT windows, but what this specific asset actually does historically. If it's 08:00 MYT and the high peak is at 14:00, the daily high likely hasn't formed yet. If it's 22:00 and the high peak was 14:00, the high is probably already in.",
      example: "High peak at 14:00 MYT, low peak at 02:00 MYT → concentrate longs in the 02:00 window.",
    },
    dow: {
      what: "Per weekday: average daily range %, bull% (fraction of days that closed above open), and sample count.",
      value: "Tells you whether to size up or down and whether to lean long or short based purely on the calendar. Low-range, near-coin-flip days (Friday) may not be worth forcing setups. Wide-range, directional days (Wednesday) are when edge compounds.",
      example: "Wednesday: 3.1% avg range, 58% bull → lean into longs, size normally. Friday: 1.8% range, 49% bull → reduce size or skip marginal setups.",
    },
    session: {
      what: "For Asia (00–07 MYT), London (14–21 MYT), and NY (20–03 MYT): what fraction of days each session made the daily high vs daily low. Sessions are non-exclusive — London/NY overlap counts in both.",
      value: "If Asia makes the low 41% of the time and price is falling during the Asia session, there's a meaningful probability you're watching the daily low form. If London makes the high 44% of the time and it's 18:00 MYT, the high may already be in — be cautious adding longs.",
      example: "Asia Lo: 41% → Asia session lows are often the daily low. London Hi: 44% → London is the most common session to make the daily high.",
    },
    weekly: {
      what: "Same P1/P2 logic but on weekly candles — was the weekly low made before the weekly high? Shows overall % plus which day of the week most commonly produces the weekly high vs weekly low.",
      value: "ICT theory says Monday often sets a weekly extreme that later gets swept. This card quantifies whether that holds for this symbol. If Tuesday makes the weekly high 34% of the time, and it's Wednesday, the weekly high is likely already in — bias bearish for the rest of the week.",
      example: "Weekly High: Tuesday 34% → if it's Thursday and price is near Monday's high, the weekly high is likely already set.",
    },
  };

  function toggleHelp(card: string): void {
    openHelp = openHelp === card ? null : card;
  }

  const formatPct = (v: number) => (v * 100).toFixed(1) + "%";
  const fmtHour = (h: number) => String(h).padStart(2, "0") + ":00";

  async function loadStats(): Promise<void> {
    loading = true;
    error = null;
    try {
      stats = await getStats(symbol, days);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  onMount(() => { void loadStats(); });

  const hourlyRows = $derived(stats ? stats.hourly_extremes : []);
  const maxHighPct = $derived(
    hourlyRows.length ? Math.max(...hourlyRows.map((r) => r.high_pct)) : 1
  );
  const maxLowPct = $derived(
    hourlyRows.length ? Math.max(...hourlyRows.map((r) => r.low_pct)) : 1
  );

  const DOW_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const p1p2Rows = $derived(
    stats
      ? DOW_ORDER.map((d) => stats.p1p2.by_dow.find((r) => r.dow === d)).filter(Boolean)
      : []
  );
</script>

<div class="page">
  <div class="page-header">
    <h2>Stats</h2>

    <div class="controls">
      <select bind:value={symbol}>
        {#each $symbols as sym}
          <option value={sym}>{sym}</option>
        {/each}
      </select>

      <select bind:value={days}>
        {#each TIMEFRAMES_DAYS as d}
          <option value={d}>{d}d</option>
        {/each}
      </select>

      <button onclick={() => void loadStats()} disabled={loading} class="btn-load">
        {loading ? "Loading…" : "Load"}
      </button>
    </div>
  </div>

  {#if error}
    <ErrorBanner message={error} />
  {/if}

  {#if loading}
    <LoadingSpinner label="Computing statistics…" />
  {:else if stats}
    <div class="grid">

      <!-- P1/P2 Daily -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">P1/P2 Daily</span>
          <button class="help-btn" class:active={openHelp === "p1p2"} onclick={() => toggleHelp("p1p2")} aria-label="Help">?</button>
        </div>
        {#if openHelp === "p1p2"}
          <div class="help-panel">
            <div class="help-section"><span class="help-label">What</span>{CARD_HELP.p1p2.what}</div>
            <div class="help-section"><span class="help-label">Value</span>{CARD_HELP.p1p2.value}</div>
            <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.p1p2.example}</div>
          </div>
        {/if}
        <div class="card-subtitle">
          Low made before High: <span class="val-accent">{formatPct(stats.p1p2.overall_p1_low_pct)}</span>
          <span class="muted"> ({stats.p1p2.sample_days}d)</span>
        </div>
        <div class="dow-bars">
          {#each p1p2Rows as row}
            {#if row}
              <div class="dow-bar-row">
                <span class="dow-label">{row.dow}</span>
                <div class="bar-track">
                  <div class="bar-fill" style="width: {(row.p1_low_pct * 100).toFixed(1)}%"></div>
                </div>
                <span class="bar-pct">{formatPct(row.p1_low_pct)}</span>
              </div>
            {/if}
          {/each}
        </div>
      </div>

      <!-- ADR -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Average Daily Range</span>
          <button class="help-btn" class:active={openHelp === "adr"} onclick={() => toggleHelp("adr")} aria-label="Help">?</button>
        </div>
        {#if openHelp === "adr"}
          <div class="help-panel">
            <div class="help-section"><span class="help-label">What</span>{CARD_HELP.adr.what}</div>
            <div class="help-section"><span class="help-label">Value</span>{CARD_HELP.adr.value}</div>
            <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.adr.example}</div>
          </div>
        {/if}
        <div class="adr-rows">
          <div class="adr-row">
            <span class="adr-label">ADR(14)</span>
            <span class="val-accent">{formatPct(stats.adr.adr_14)}</span>
          </div>
          <div class="adr-row">
            <span class="adr-label">ADR(30)</span>
            <span class="val-accent">{formatPct(stats.adr.adr_30)}</span>
          </div>
          {#if stats.adr.today_range_pct !== null}
            <div class="adr-row">
              <span class="adr-label">Today</span>
              <span class="val-accent">
                {formatPct(stats.adr.today_range_pct)}
                {#if stats.adr.today_consumed_pct !== null}
                  <span class="muted">({formatPct(stats.adr.today_consumed_pct)} used)</span>
                {/if}
              </span>
            </div>
            {#if stats.adr.today_consumed_pct !== null}
              <div class="adr-gauge-track">
                <div
                  class="adr-gauge-fill"
                  style="width: {Math.min(stats.adr.today_consumed_pct * 100, 100).toFixed(1)}%"
                ></div>
              </div>
            {/if}
          {/if}
        </div>
      </div>

      <!-- Hourly Extreme Distribution -->
      <div class="card card-wide">
        <div class="card-header">
          <span class="card-title">Hourly Extreme Distribution (MYT)</span>
          <button class="help-btn" class:active={openHelp === "hourly"} onclick={() => toggleHelp("hourly")} aria-label="Help">?</button>
        </div>
        {#if openHelp === "hourly"}
          <div class="help-panel">
            <div class="help-section"><span class="help-label">What</span>{CARD_HELP.hourly.what}</div>
            <div class="help-section"><span class="help-label">Value</span>{CARD_HELP.hourly.value}</div>
            <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.hourly.example}</div>
          </div>
        {/if}
        <div class="card-subtitle muted">
          High peak: <span class="val-green">{fmtHour(stats.hourly_extremes.reduce((a, b) => a.high_pct > b.high_pct ? a : b).hour_myt)} MYT</span>
          &nbsp;·&nbsp;
          Low peak: <span class="val-red">{fmtHour(stats.hourly_extremes.reduce((a, b) => a.low_pct > b.low_pct ? a : b).hour_myt)} MYT</span>
        </div>
        <div class="hourly-chart">
          {#each hourlyRows as row}
            <div class="hour-col">
              <div class="hour-bars">
                <div
                  class="hour-bar high"
                  style="height: {maxHighPct > 0 ? (row.high_pct / maxHighPct * 60).toFixed(1) : 0}px"
                  title="High {formatPct(row.high_pct)}"
                ></div>
                <div
                  class="hour-bar low"
                  style="height: {maxLowPct > 0 ? (row.low_pct / maxLowPct * 60).toFixed(1) : 0}px"
                  title="Low {formatPct(row.low_pct)}"
                ></div>
              </div>
              <span class="hour-label">{row.hour_myt}</span>
            </div>
          {/each}
        </div>
        <div class="hourly-legend">
          <span class="legend-high">■ High</span>
          <span class="legend-low">■ Low</span>
        </div>
      </div>

      <!-- DOW Patterns -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Day-of-Week Patterns</span>
          <button class="help-btn" class:active={openHelp === "dow"} onclick={() => toggleHelp("dow")} aria-label="Help">?</button>
        </div>
        {#if openHelp === "dow"}
          <div class="help-panel">
            <div class="help-section"><span class="help-label">What</span>{CARD_HELP.dow.what}</div>
            <div class="help-section"><span class="help-label">Value</span>{CARD_HELP.dow.value}</div>
            <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.dow.example}</div>
          </div>
        {/if}
        <table class="stat-table">
          <thead>
            <tr>
              <th>Day</th>
              <th>Avg Range</th>
              <th>Bull %</th>
              <th>N</th>
            </tr>
          </thead>
          <tbody>
            {#each stats.dow_patterns as row}
              <tr>
                <td class="val-muted">{row.dow}</td>
                <td>{formatPct(row.avg_range_pct)}</td>
                <td class:bull={row.bull_pct >= 0.5} class:bear={row.bull_pct < 0.5}>
                  {formatPct(row.bull_pct)}
                </td>
                <td class="val-muted">{row.sample_days}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>

      <!-- Session Breakdown -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Session Breakdown</span>
          <button class="help-btn" class:active={openHelp === "session"} onclick={() => toggleHelp("session")} aria-label="Help">?</button>
        </div>
        {#if openHelp === "session"}
          <div class="help-panel">
            <div class="help-section"><span class="help-label">What</span>{CARD_HELP.session.what}</div>
            <div class="help-section"><span class="help-label">Value</span>{CARD_HELP.session.value}</div>
            <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.session.example}</div>
          </div>
        {/if}
        <table class="stat-table">
          <thead>
            <tr>
              <th>Session</th>
              <th>Hi %</th>
              <th>Lo %</th>
            </tr>
          </thead>
          <tbody>
            {#each stats.sessions as row}
              <tr>
                <td class="val-muted">{row.session}</td>
                <td class="val-green">{formatPct(row.high_pct)}</td>
                <td class="val-red">{formatPct(row.low_pct)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
        <div class="session-note muted">Sessions in MYT · London/NY overlap counted in both</div>
      </div>

      <!-- Weekly P1/P2 -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Weekly P1/P2</span>
          <button class="help-btn" class:active={openHelp === "weekly"} onclick={() => toggleHelp("weekly")} aria-label="Help">?</button>
        </div>
        {#if openHelp === "weekly"}
          <div class="help-panel">
            <div class="help-section"><span class="help-label">What</span>{CARD_HELP.weekly.what}</div>
            <div class="help-section"><span class="help-label">Value</span>{CARD_HELP.weekly.value}</div>
            <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.weekly.example}</div>
          </div>
        {/if}
        <div class="weekly-rows">
          <div class="weekly-row">
            <span class="weekly-label">Low first</span>
            <span class="val-accent">{formatPct(stats.weekly_p1p2.overall_p1_low_pct)}</span>
            <span class="muted">({stats.weekly_p1p2.sample_weeks} wks)</span>
          </div>
          <div class="weekly-row">
            <span class="weekly-label">Weekly High</span>
            <span class="val-green">{stats.weekly_p1p2.high_day}</span>
          </div>
          <div class="weekly-row">
            <span class="weekly-label">Weekly Low</span>
            <span class="val-red">{stats.weekly_p1p2.low_day}</span>
          </div>
        </div>
      </div>

    </div>
  {/if}
</div>

<style>
  .page {
    padding: 20px;
    max-width: 1200px;
    margin: 0 auto;
  }

  .page-header {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 20px;
    flex-wrap: wrap;
  }

  .page-header h2 {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--text);
  }

  .controls {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
  }

  select {
    background: var(--bg-input, var(--bg-panel));
    border: 1px solid var(--border);
    color: var(--text);
    padding: 5px 10px;
    font-size: 12px;
    border-radius: 4px;
    cursor: pointer;
  }

  .btn-load {
    background: var(--accent);
    color: #000;
    border: none;
    padding: 5px 14px;
    font-size: 12px;
    font-weight: 600;
    border-radius: 4px;
    cursor: pointer;
    letter-spacing: 0.05em;
    transition: opacity 120ms;
  }

  .btn-load:disabled { opacity: 0.5; cursor: default; }

  /* Grid layout */
  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }

  .card-wide {
    grid-column: 1 / -1;
  }

  @media (max-width: 700px) {
    .grid { grid-template-columns: 1fr; }
    .card-wide { grid-column: 1; }
  }

  .card {
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 16px;
  }

  /* Card header with help button */
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
  }

  .card-title {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--accent);
  }

  .help-btn {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--muted);
    font-size: 9px;
    font-weight: 700;
    line-height: 1;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    transition: border-color 120ms, color 120ms, background 120ms;
    flex-shrink: 0;
  }

  .help-btn:hover {
    border-color: var(--accent);
    color: var(--accent);
  }

  .help-btn.active {
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    border-color: var(--accent);
    color: var(--accent);
  }

  /* Help panel */
  .help-panel {
    background: color-mix(in srgb, var(--accent) 5%, var(--bg, #111));
    border: 1px solid color-mix(in srgb, var(--accent) 20%, transparent);
    border-radius: 4px;
    padding: 10px 12px;
    margin-bottom: 12px;
    display: flex;
    flex-direction: column;
    gap: 7px;
    animation: help-in 150ms ease;
  }

  @keyframes help-in {
    from { opacity: 0; transform: translateY(-4px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  .help-section {
    font-size: 11px;
    color: var(--text-dim);
    line-height: 1.5;
    display: grid;
    grid-template-columns: 38px 1fr;
    gap: 6px;
  }

  .help-label {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
    padding-top: 1px;
  }

  .help-example {
    color: color-mix(in srgb, var(--accent) 70%, var(--text-dim));
    font-style: italic;
  }

  .card-subtitle {
    font-size: 12px;
    color: var(--text-dim);
    margin-bottom: 12px;
  }

  .muted { color: var(--muted); }
  .val-accent { color: var(--accent); }
  .val-green { color: var(--green, #4caf81); }
  .val-red { color: var(--red, #e05c5c); }
  .val-muted { color: var(--text-dim); }

  /* P1/P2 DOW bars */
  .dow-bars { display: flex; flex-direction: column; gap: 5px; }

  .dow-bar-row {
    display: grid;
    grid-template-columns: 32px 1fr 44px;
    align-items: center;
    gap: 8px;
    font-size: 11px;
  }

  .dow-label { color: var(--text-dim); font-size: 11px; }

  .bar-track {
    background: var(--bg, #1a1a1a);
    border-radius: 2px;
    height: 6px;
    overflow: hidden;
  }

  .bar-fill {
    background: var(--accent);
    height: 100%;
    border-radius: 2px;
    transition: width 300ms ease;
  }

  .bar-pct { font-size: 11px; color: var(--text-dim); text-align: right; }

  /* ADR */
  .adr-rows { display: flex; flex-direction: column; gap: 8px; }

  .adr-row {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
  }

  .adr-label { color: var(--text-dim); }

  .adr-gauge-track {
    background: var(--bg, #1a1a1a);
    border-radius: 3px;
    height: 8px;
    overflow: hidden;
    margin-top: 4px;
  }

  .adr-gauge-fill {
    background: linear-gradient(90deg, var(--accent) 0%, #e0a030 100%);
    height: 100%;
    border-radius: 3px;
    transition: width 300ms ease;
  }

  /* Hourly chart */
  .hourly-chart {
    display: flex;
    align-items: flex-end;
    gap: 2px;
    padding: 8px 0 0;
    overflow-x: auto;
  }

  .hour-col {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    min-width: 20px;
    flex: 1;
  }

  .hour-bars {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-end;
    gap: 1px;
    height: 62px;
  }

  .hour-bar {
    width: 8px;
    border-radius: 1px 1px 0 0;
    min-height: 2px;
    transition: height 300ms ease;
  }

  .hour-bar.high { background: var(--green, #4caf81); }
  .hour-bar.low  { background: var(--red, #e05c5c); }

  .hour-label {
    font-size: 9px;
    color: var(--muted);
    font-feature-settings: "tnum" 1;
  }

  .hourly-legend {
    display: flex;
    gap: 16px;
    margin-top: 8px;
    font-size: 11px;
  }

  .legend-high { color: var(--green, #4caf81); }
  .legend-low  { color: var(--red, #e05c5c); }

  /* Tables */
  .stat-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }

  .stat-table th {
    text-align: left;
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.08em;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    padding: 4px 6px;
  }

  .stat-table td {
    padding: 5px 6px;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 50%, transparent);
    color: var(--text);
  }

  .stat-table tr:last-child td { border-bottom: none; }

  .bull { color: var(--green, #4caf81); }
  .bear { color: var(--red, #e05c5c); }

  .session-note {
    font-size: 10px;
    margin-top: 8px;
  }

  /* Weekly P1/P2 */
  .weekly-rows { display: flex; flex-direction: column; gap: 10px; }

  .weekly-row {
    display: flex;
    align-items: baseline;
    gap: 8px;
    font-size: 13px;
  }

  .weekly-label {
    color: var(--text-dim);
    font-size: 11px;
    min-width: 80px;
  }
</style>
