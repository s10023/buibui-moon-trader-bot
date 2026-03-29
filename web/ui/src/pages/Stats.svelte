<script lang="ts">
  import { onMount } from "svelte";
  import { getStats, type StatsResponse } from "../api";
  import { symbols } from "../stores/config";
  import LoadingSpinner from "../components/LoadingSpinner.svelte";
  import ErrorBanner from "../components/ErrorBanner.svelte";

  const TIMEFRAMES_DAYS = [30, 90, 180, 365];
  const DOW_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  let symbol = $state("BTCUSDT");
  let days = $state(180);
  let stats = $state<StatsResponse | null>(null);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let openHelp = $state<string | null>(null);

  // P1/P2 card: "low_first" = bullish context (low forms first); "high_first" = bearish context
  let p1p2Mode = $state<"low_first" | "high_first">("low_first");
  // Weekly card: default "high_first" (bearish — when does weekly high form?)
  let weeklyMode = $state<"low_first" | "high_first">("high_first");

  // Live MYT clock — ticks every minute
  let now = $state(Date.now());

  onMount(() => {
    void loadStats();
    const t = setInterval(() => { now = Date.now(); }, 60_000);
    return () => clearInterval(t);
  });

  // MYT = UTC+8: shift timestamp then read UTC fields
  const currentMYTHour = $derived(new Date(now + 8 * 3600 * 1000).getUTCHours());
  const todayDOW = $derived(
    ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][new Date(now + 8 * 3600 * 1000).getUTCDay()]
  );

  // Active sessions based on current MYT hour (sessions non-exclusive — London/NY overlap 20–21)
  const activeSessions = $derived(
    [
      currentMYTHour >= 0 && currentMYTHour <= 7 ? "Asia" : null,
      currentMYTHour >= 14 && currentMYTHour <= 21 ? "London" : null,
      (currentMYTHour >= 20 || currentMYTHour <= 3) ? "NY" : null,
    ].filter(Boolean) as string[]
  );

  const CARD_HELP: Record<string, { what: string; value: string; example: string }> = {
    p1p2: {
      what: "For each historical day, was the daily LOW or HIGH made first? Toggle 'Low First' (bullish context — dip then recovery) vs 'High First' (bearish — pump then dump). Today's day of week is highlighted.",
      value: "Before entering a long on a dip, check if today's DOW historically makes the low first. If P1=Low is 62% on Thursdays, the day structure is behind you. If you're shorting a Thursday rally, you're fighting historical bias.",
      example: "Thursday P1=Low 62% → low forms first then recovers. Favour longs on Thursday dips.",
    },
    adr: {
      what: "ADR(14) = 2-week rolling average range — short-term volatility. ADR(30) = monthly baseline. Both are (daily high − low) / open. The 'Today' gauge shows how much of the typical daily range is already consumed. Note: DOW Patterns 'Avg Range' is the historical average for that specific weekday type across the full lookback — a different metric.",
      value: "If today's gauge is 90%+ consumed, the move is done — don't chase entries. If ADR(14) >> ADR(30), the market is recently more volatile than usual — widen your stops.",
      example: "ADR(14) 2.8%, today 1.2% → 43% consumed. Room to run.",
    },
    hourly: {
      what: "24-column chart: fraction of days each MYT hour made the daily high (green) vs daily low (red). Current hour is highlighted with a border. Peaks are labelled.",
      value: "Your empirically-derived kill zone for this specific asset — not fixed ICT windows. If the high peak is at 14:00 and it's only 08:00, the high hasn't formed yet. If the high peak was at 14:00 and it's now 20:00, the high is likely already in.",
      example: "High peak 14:00, low peak 02:00 MYT → buy the 02:00 dip, target 14:00 expansion.",
    },
    dow: {
      what: "Per weekday: avg daily range with a relative bar, bull/bear split shown as a coloured bar + bull%, and N (sample count = how many of that weekday appear in the lookback window, e.g. ~26 Mondays in 180d). Today's row is highlighted.",
      value: "Size up on wide-range directional days. Reduce size or skip on low-range coin-flip days. N tells you how many data points back each bar.",
      example: "Wed 3.1% range, 58% bull → lean longs, size normally. Fri 1.8%, 49% → skip marginal setups.",
    },
    session: {
      what: "Asia (00–07 MYT), London (14–21 MYT), NY (20–03 MYT): fraction of days each session made the daily high or low. Sessions are non-exclusive — the London/NY overlap window (20–21 MYT) counts in both, so columns don't sum to 100%. Active sessions shown with ●.",
      value: "If Asia makes the daily low 41% of the time and price is falling during Asia session, there's meaningful probability you're watching the daily low form.",
      example: "Asia Lo 41% + price dropping in Asia → probable daily low forming. Watch for reversal.",
    },
    weekly: {
      what: "Which day of the week most commonly forms the weekly extreme. Bear context shows when the weekly HIGH forms (useful for shorts). Bull context shows when the weekly LOW forms (useful for longs). Bars show % of weeks each DOW made that extreme, normalized to the dominant day.",
      value: "ICT: Monday often sets a weekly extreme that gets swept later. This card quantifies it per symbol. If Tue makes the weekly high 34% of the time and it's Wednesday, the weekly high is likely already set.",
      example: "Weekly High: Tue 34% — if it's Thu, weekly high likely in. Bias: avoid adding longs.",
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

  const hourlyRows = $derived(stats ? stats.hourly_extremes : []);
  const maxHighPct = $derived(hourlyRows.length ? Math.max(...hourlyRows.map((r) => r.high_pct)) : 1);
  const maxLowPct  = $derived(hourlyRows.length ? Math.max(...hourlyRows.map((r) => r.low_pct)) : 1);

  const p1p2Rows = $derived(
    stats
      ? DOW_ORDER.map((d) => stats.p1p2.by_dow.find((r) => r.dow === d)).filter(Boolean)
      : []
  );

  const maxDOWRange = $derived(
    stats ? Math.max(...stats.dow_patterns.map((r) => r.avg_range_pct)) : 1
  );

  const weeklyBars = $derived(
    stats
      ? DOW_ORDER.map((d) => ({
          dow: d,
          pct: weeklyMode === "high_first"
            ? (stats.weekly_p1p2.high_by_dow?.[d] ?? 0)
            : (stats.weekly_p1p2.low_by_dow?.[d] ?? 0),
        }))
      : []
  );

  const weeklyMaxPct = $derived(
    weeklyBars.length ? (Math.max(...weeklyBars.map((r) => r.pct)) || 1) : 1
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

      <div class="days-group">
        <select bind:value={days}>
          {#each TIMEFRAMES_DAYS as d}
            <option value={d}>{d}d</option>
          {/each}
        </select>
        <span class="days-note">lookback (ADR always uses last 14/30d)</span>
      </div>

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
          <div class="header-actions">
            <div class="pill-toggle">
              <button class:active={p1p2Mode === "low_first"} onclick={() => { p1p2Mode = "low_first"; }}>Low First</button>
              <button class:active={p1p2Mode === "high_first"} onclick={() => { p1p2Mode = "high_first"; }}>High First</button>
            </div>
            <button class="help-btn" class:active={openHelp === "p1p2"} onclick={() => toggleHelp("p1p2")} aria-label="Help">?</button>
          </div>
        </div>
        {#if openHelp === "p1p2"}
          <div class="help-panel">
            <div class="help-section"><span class="help-label">What</span>{CARD_HELP.p1p2.what}</div>
            <div class="help-section"><span class="help-label">Value</span>{CARD_HELP.p1p2.value}</div>
            <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.p1p2.example}</div>
          </div>
        {/if}
        <div class="card-subtitle">
          {#if p1p2Mode === "low_first"}
            Low made before High: <span class="val-accent">{formatPct(stats.p1p2.overall_p1_low_pct)}</span>
          {:else}
            High made before Low: <span class="val-accent">{formatPct(1 - stats.p1p2.overall_p1_low_pct)}</span>
          {/if}
          <span class="muted"> ({stats.p1p2.sample_days}d sampled)</span>
        </div>
        <div class="dow-bars">
          {#each p1p2Rows as row}
            {#if row}
              {@const pct = p1p2Mode === "low_first" ? row.p1_low_pct : 1 - row.p1_low_pct}
              <div class="dow-bar-row" class:today-row={row.dow === todayDOW}>
                <span class="dow-label">{row.dow}</span>
                <div class="bar-track">
                  <div class="bar-fill" style="width: {(pct * 100).toFixed(1)}%"></div>
                </div>
                <span class="bar-pct">{formatPct(pct)}</span>
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
            <span class="adr-label">ADR(14) <span class="adr-sublabel">2-week</span></span>
            <span class="val-accent">{formatPct(stats.adr.adr_14)}</span>
          </div>
          <div class="adr-row">
            <span class="adr-label">ADR(30) <span class="adr-sublabel">monthly</span></span>
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
              {@const consumed = stats.adr.today_consumed_pct}
              <div class="adr-gauge-track">
                <div
                  class="adr-gauge-fill"
                  class:adr-gauge-warn={consumed >= 0.8}
                  style="width: {Math.min(consumed * 100, 100).toFixed(1)}%"
                ></div>
              </div>
              {#if consumed >= 0.8}
                <div class="adr-warn">Range nearly consumed — avoid chasing entries</div>
              {/if}
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
          &nbsp;·&nbsp;
          Now: <span class="val-accent">{fmtHour(currentMYTHour)} MYT</span>
        </div>
        <div class="hourly-chart">
          {#each hourlyRows as row}
            <div class="hour-col" class:hour-now={row.hour_myt === currentMYTHour}>
              <div class="hour-bars">
                <div
                  class="hour-bar high"
                  style="height: {maxHighPct > 0 ? (row.high_pct / maxHighPct * 60).toFixed(1) : 0}px"
                  title="{fmtHour(row.hour_myt)} High {formatPct(row.high_pct)}"
                ></div>
                <div
                  class="hour-bar low"
                  style="height: {maxLowPct > 0 ? (row.low_pct / maxLowPct * 60).toFixed(1) : 0}px"
                  title="{fmtHour(row.hour_myt)} Low {formatPct(row.low_pct)}"
                ></div>
              </div>
              <span class="hour-label">{row.hour_myt}</span>
            </div>
          {/each}
        </div>
        <div class="hourly-legend">
          <span class="legend-high">■ High</span>
          <span class="legend-low">■ Low</span>
          <span class="legend-now">▼ Now</span>
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
              <th>Direction</th>
              <th title="Number of that weekday in the lookback window">N</th>
            </tr>
          </thead>
          <tbody>
            {#each stats.dow_patterns as row}
              <tr class:today-row={row.dow === todayDOW}>
                <td class="dow-name">{row.dow}</td>
                <td>
                  <div class="range-cell">
                    <div class="range-mini">
                      <div class="range-mini-fill" style="width: {(row.avg_range_pct / maxDOWRange * 100).toFixed(0)}%"></div>
                    </div>
                    <span>{formatPct(row.avg_range_pct)}</span>
                  </div>
                </td>
                <td>
                  <div class="bias-cell">
                    <div class="bias-split">
                      <div class="bias-bull" style="width: {(row.bull_pct * 100).toFixed(0)}%"></div>
                      <div class="bias-bear" style="width: {((1 - row.bull_pct) * 100).toFixed(0)}%"></div>
                    </div>
                    <span class:bull={row.bull_pct >= 0.5} class:bear={row.bull_pct < 0.5}>
                      {formatPct(row.bull_pct)}
                    </span>
                  </div>
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
              <th>Session (MYT)</th>
              <th>Hi %</th>
              <th>Lo %</th>
            </tr>
          </thead>
          <tbody>
            {#each stats.sessions as row}
              {@const isActive = activeSessions.includes(row.session)}
              <tr class:session-active={isActive}>
                <td class="session-name-cell">
                  {row.session}
                  {#if isActive}<span class="live-dot" title="Active now">●</span>{/if}
                </td>
                <td class="val-green">{formatPct(row.high_pct)}</td>
                <td class="val-red">{formatPct(row.low_pct)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
        <div class="session-note muted">London/NY overlap (20–21 MYT) counted in both — columns don't sum to 100%</div>
      </div>

      <!-- Weekly P1/P2 -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Weekly P1/P2</span>
          <div class="header-actions">
            <div class="pill-toggle">
              <button class:active={weeklyMode === "high_first"} onclick={() => { weeklyMode = "high_first"; }}>Bear</button>
              <button class:active={weeklyMode === "low_first"} onclick={() => { weeklyMode = "low_first"; }}>Bull</button>
            </div>
            <button class="help-btn" class:active={openHelp === "weekly"} onclick={() => toggleHelp("weekly")} aria-label="Help">?</button>
          </div>
        </div>
        {#if openHelp === "weekly"}
          <div class="help-panel">
            <div class="help-section"><span class="help-label">What</span>{CARD_HELP.weekly.what}</div>
            <div class="help-section"><span class="help-label">Value</span>{CARD_HELP.weekly.value}</div>
            <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.weekly.example}</div>
          </div>
        {/if}
        <div class="card-subtitle">
          {#if weeklyMode === "high_first"}
            Weekly high before low: <span class="val-accent">{formatPct(1 - stats.weekly_p1p2.overall_p1_low_pct)}</span>
            · Most common: <span class="val-red">{stats.weekly_p1p2.high_day}</span>
          {:else}
            Weekly low before high: <span class="val-accent">{formatPct(stats.weekly_p1p2.overall_p1_low_pct)}</span>
            · Most common: <span class="val-green">{stats.weekly_p1p2.low_day}</span>
          {/if}
          <span class="muted"> ({stats.weekly_p1p2.sample_weeks} wks)</span>
        </div>
        <div class="dow-bars">
          {#each weeklyBars as row}
            <div class="dow-bar-row" class:today-row={row.dow === todayDOW}>
              <span class="dow-label">{row.dow}</span>
              <div class="bar-track">
                <div
                  class="bar-fill"
                  class:bar-fill-bear={weeklyMode === "high_first"}
                  style="width: {weeklyMaxPct > 0 ? (row.pct / weeklyMaxPct * 100).toFixed(1) : 0}%"
                ></div>
              </div>
              <span class="bar-pct">{formatPct(row.pct)}</span>
            </div>
          {/each}
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

  .days-group {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .days-note {
    font-size: 10px;
    color: var(--muted);
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

  .card-wide { grid-column: 1 / -1; }

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

  /* Card header */
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
    gap: 8px;
  }

  .card-title {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--accent);
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }

  /* Pill toggle */
  .pill-toggle {
    display: flex;
    border: 1px solid var(--border);
    border-radius: 4px;
    overflow: hidden;
  }

  .pill-toggle button {
    background: transparent;
    border: none;
    color: var(--muted);
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 3px 7px;
    cursor: pointer;
    transition: background 120ms, color 120ms;
  }

  .pill-toggle button + button {
    border-left: 1px solid var(--border);
  }

  .pill-toggle button.active {
    background: color-mix(in srgb, var(--accent) 15%, transparent);
    color: var(--accent);
  }

  .pill-toggle button:hover:not(.active) {
    color: var(--text);
  }

  /* Help button */
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

  .help-btn:hover { border-color: var(--accent); color: var(--accent); }
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

  /* P1/P2 + Weekly DOW bars */
  .dow-bars { display: flex; flex-direction: column; gap: 5px; }

  .dow-bar-row {
    display: grid;
    grid-template-columns: 32px 1fr 44px;
    align-items: center;
    gap: 8px;
    font-size: 11px;
    border-radius: 3px;
    padding: 2px 4px;
    margin: 0 -4px;
    transition: background 150ms;
  }

  .dow-bar-row.today-row {
    background: color-mix(in srgb, var(--accent) 7%, transparent);
  }

  .dow-label {
    color: var(--text-dim);
    font-size: 11px;
  }

  .dow-bar-row.today-row .dow-label {
    color: var(--accent);
    font-weight: 700;
  }

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

  .bar-fill.bar-fill-bear {
    background: var(--red, #e05c5c);
  }

  .bar-pct { font-size: 11px; color: var(--text-dim); text-align: right; }

  /* ADR */
  .adr-rows { display: flex; flex-direction: column; gap: 8px; }

  .adr-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    font-size: 12px;
  }

  .adr-label { color: var(--text-dim); }

  .adr-sublabel {
    font-size: 9px;
    color: var(--muted);
    font-style: normal;
    margin-left: 4px;
  }

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

  .adr-gauge-fill.adr-gauge-warn {
    background: linear-gradient(90deg, #e0a030 0%, var(--red, #e05c5c) 100%);
  }

  .adr-warn {
    font-size: 10px;
    color: var(--red, #e05c5c);
    margin-top: 4px;
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
    border-radius: 3px;
    padding: 2px 1px;
  }

  .hour-col.hour-now {
    outline: 1px solid color-mix(in srgb, var(--accent) 50%, transparent);
    background: color-mix(in srgb, var(--accent) 7%, transparent);
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

  .hour-col.hour-now .hour-label {
    color: var(--accent);
    font-weight: 700;
  }

  .hourly-legend {
    display: flex;
    gap: 16px;
    margin-top: 8px;
    font-size: 11px;
  }

  .legend-high { color: var(--green, #4caf81); }
  .legend-low  { color: var(--red, #e05c5c); }
  .legend-now  { color: var(--accent); }

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

  /* DOW today highlight in table */
  tr.today-row td { background: color-mix(in srgb, var(--accent) 6%, transparent); }
  tr.today-row .dow-name { color: var(--accent); font-weight: 700; }

  /* DOW range mini bar */
  .range-cell {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .range-mini {
    width: 36px;
    height: 4px;
    background: var(--bg, #1a1a1a);
    border-radius: 2px;
    overflow: hidden;
    flex-shrink: 0;
  }

  .range-mini-fill {
    height: 100%;
    background: color-mix(in srgb, var(--accent) 60%, transparent);
    border-radius: 2px;
  }

  /* DOW direction split bar */
  .bias-cell {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .bias-split {
    display: flex;
    width: 36px;
    height: 4px;
    border-radius: 2px;
    overflow: hidden;
    flex-shrink: 0;
  }

  .bias-bull { background: var(--green, #4caf81); }
  .bias-bear { background: var(--red, #e05c5c); }

  .bull { color: var(--green, #4caf81); }
  .bear { color: var(--red, #e05c5c); }

  /* Session */
  tr.session-active td { background: color-mix(in srgb, var(--accent) 5%, transparent); }
  tr.session-active .session-name-cell { color: var(--accent); }

  .session-name-cell { color: var(--text-dim); }

  .live-dot {
    color: var(--accent);
    font-size: 8px;
    margin-left: 4px;
    animation: pulse 2s infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.35; }
  }

  .session-note {
    font-size: 10px;
    margin-top: 8px;
  }
</style>
