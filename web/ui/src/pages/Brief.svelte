<script lang="ts">
  import { onMount } from "svelte";
  import { getBrief, type BriefResponse } from "../api";
  import LoadingSpinner from "../components/LoadingSpinner.svelte";
  import ErrorBanner from "../components/ErrorBanner.svelte";

  let data = $state<BriefResponse | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let showLegend = $state(false);

  const REGIME_LABEL: Record<string, string> = {
    trend: "Trend",
    range: "Range",
    high_vol: "High-Vol",
    unknown: "Unknown",
  };

  const fmtPrice = (v: number): string =>
    v >= 1000
      ? v.toLocaleString("en-US", { maximumFractionDigits: 0 })
      : v >= 1
        ? v.toFixed(2)
        : v.toFixed(4);

  const fmtDist = (v: number): string => (v >= 0 ? `+${v.toFixed(2)}` : v.toFixed(2));
  // Rounding: JS Math.round is half-up; the markdown renderer's fmt_frac is
  // Python "%.0f" (half-to-even). These surfaces are independent, so a
  // sub-percent half-point tie can round differently — accepted as cosmetic.
  const fmtPct = (v: number | null): string => (v === null ? "—" : `${Math.round(v * 100)}%`);
  const fmtR = (v: number | null): string =>
    v === null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(2);

  const asOfMyt = (ms: number): string =>
    new Date(ms).toLocaleString("en-MY", {
      timeZone: "Asia/Kuala_Lumpur",
      dateStyle: "medium",
      timeStyle: "short",
    });

  const MYT_MS = 28_800_000;
  const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const mytHHMM = (ms: number): string => new Date(ms + MYT_MS).toISOString().slice(11, 16);
  const mytHH = (ms: number): string => new Date(ms + MYT_MS).toISOString().slice(11, 13);
  const mytDow = (ms: number): string => DOW[(new Date(ms + MYT_MS).getUTCDay() + 6) % 7];

  async function load(): Promise<void> {
    loading = true;
    error = null;
    try {
      data = await getBrief();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    void load();
  });
</script>

<div class="page">
  <div class="page-header">
    <h2>Daily Brief</h2>
    <div class="controls">
      {#if data}
        <span class="day-ahead">{data.day_ahead}</span>
        <span class="muted">as-of {asOfMyt(data.as_of_ms)} MYT</span>
        <span class="health-pill" class:ok={data.health.data_ok} class:warn={!data.health.data_ok}>
          {data.health.data_ok ? "● data ok" : "⚠ data issue"}
        </span>
        {#if data.session_clock}
          {@const clk = data.session_clock}
          <span class="clock-pill">
            {clk.label === "Off" ? "between sessions" : clk.label}{clk.is_overlap
              ? " (NY overlap)"
              : ""}
            · next {clk.next_label} {mytHHMM(clk.next_start_ms)} MYT
          </span>
        {/if}
      {/if}
      <button class="btn-refresh" onclick={() => (showLegend = !showLegend)}>
        ⓘ legend
      </button>
      <button class="btn-refresh" onclick={() => void load()} disabled={loading}>
        {loading ? "Loading…" : "Refresh"}
      </button>
    </div>
  </div>

  {#if showLegend}
    <div class="card legend-card">
      <div class="card-header"><span class="card-title">Reading this brief</span></div>
      <dl class="legend-list muted">
        <dt>Regime 1D / 4H</dt>
        <dd>
          Classifier output (trend / range / high-vol / unknown) from the same
          regime model that soft-gates live signals.
        </dd>
        <dt>ATR14</dt>
        <dd>
          Daily Wilder ATR in price units — the yardstick: every ± number on
          levels and zones is a distance in daily ATRs from the Last price.
        </dd>
        <dt>Levels</dt>
        <dd>
          PDH/PDL prior day high/low · PWH/PWL prior week · MonH/MonL Monday's
          range (active Tue onward) · DO/WO/MO today's / this week's / this
          month's open.
        </dd>
        <dt>swept</dt>
        <dd>
          Price pierced the level during the current period and now trades back
          on the original side (sweep + reclaim/reject).
        </dd>
        <dt>Zones</dt>
        <dd>
          Structural zones per timeframe — FVG fair-value gap · OB order block ·
          BOS break of structure · EQH/EQL equal highs/lows. Tag shows ATR
          distance, or "inside" when price is within the zone.
        </dd>
        <dt>Last</dt>
        <dd>
          Reference price for all distances: freshest completed 1h close,
          falling back to the forming or last completed daily bar (tagged).
        </dd>
        <dt>Seasonality</dt>
        <dd>
          Day-of-week stats over the lookback: bull % of days, average range,
          which session most often prints the day's high/low, and typical
          weekly high/low days.
        </dd>
        <dt>EMA</dt>
        <dd>
          Price vs the 20/50/200-day EMAs (▲ above / ▼ below / — not enough
          history), the stack order (bullish 20&gt;50&gt;200), and the 200's
          5-day slope.
        </dd>
        <dt>State</dt>
        <dd>
          Current 1D regime and how many bars it has held; when ranging, the
          run's high–low band and where price sits inside it.
        </dd>
        <dt>Monday</dt>
        <dd>
          Price vs this week's Monday range (above / inside / below); "forming"
          on Mondays while the range is still being set.
        </dd>
        <dt>Candle</dt>
        <dd>
          Anatomy patterns detected on yesterday's completed daily candle
          (engulfing, pin bar, doji, inside bar, hammer, star).
        </dd>
        <dt>PA</dt>
        <dd>
          Price-action character over the last 10 days: impulse (fast
          directional) vs grind (slow directional) vs chop, from efficiency
          ratio × ATR-normalised speed.
        </dd>
        <dt>BB/VWAP</dt>
        <dd>
          Bollinger(20,2σ) %B and bandwidth (p = squeeze percentile), plus
          price distance in ATRs from the weekly (W) and monthly (M) anchored
          VWAPs.
        </dd>
        <dt>VP 60d</dt>
        <dd>
          60-day volume profile from our own 1h data: point of control and 70%
          value area, with price above / inside / below value.
        </dd>
        <dt>Session clock</dt>
        <dd>
          Current MYT trading session: Asia 08–14 · London 14–22 · NY 22–04;
          20–22 = London–NY overlap. "between sessions" = the Off gap.
        </dd>
        <dt>Sessions</dt>
        <dd>
          Last 3 completed sessions: net move and range in ATR14 units.
          "(n/m bars)" flags partial 1h coverage.
        </dd>
        <dt>·set-high / ·set-low</dt>
        <dd>Which of the 3 sessions printed the day's high / low.</dd>
        <dt>tendency</dt>
        <dd>
          Share of the last 180 days each session made the daily high or low.
        </dd>
        <dt>Pundit board</dt>
        <dd>
          Recent ledger calls with per-author priors: n calls scored, hit
          rate, and average R (ATR-proxy R for stop-less calls). ⚠ marks
          low-sample authors; ● marks calls on a symbol shown above.
        </dd>
      </dl>
    </div>
  {/if}

  {#if error}
    <ErrorBanner {error} />
  {/if}

  {#if loading && !data}
    <LoadingSpinner label="Computing brief…" />
  {:else if data}
    <div class="grid panels-grid">
      {#each data.panels as panel (panel.symbol)}
        <div class="card panel-card">
          <div class="card-header">
            <span class="card-title">{panel.symbol}</span>
            {#if !panel.error}
              <div class="regime-chips">
                <span class="regime-chip regime-{panel.regime_1d}">
                  1D {REGIME_LABEL[panel.regime_1d] ?? panel.regime_1d}
                </span>
                <span class="regime-chip regime-{panel.regime_4h}">
                  4H {REGIME_LABEL[panel.regime_4h] ?? panel.regime_4h}
                </span>
              </div>
            {/if}
          </div>

          {#if panel.error}
            <ErrorBanner error={panel.error} />
          {:else}
            <div class="panel-meta muted">
              ATR14 {fmtPrice(panel.atr14)}
              {#if panel.adr_pct !== null}· ADR {fmtPct(panel.adr_pct)}{/if}
            </div>

            {#if panel.indicators}
              {@const ind = panel.indicators}
              <div class="indicators muted">
                {#if ind.ema}
                  <div class="ind-row">
                    <span class="ind-label">EMA</span>
                    <span>
                      {#each [[ind.ema.above_20, 20], [ind.ema.above_50, 50], [ind.ema.above_200, 200]] as [above, span]}
                        <span
                          class="ema-bit"
                          class:pos={above === true}
                          class:neg={above === false}
                        >
                          {above === null ? "—" : above ? "▲" : "▼"}{span}
                        </span>
                      {/each}
                      {#if ind.ema.stack}· stack {ind.ema.stack}{/if}
                      {#if ind.ema.slope_200}· 200 {ind.ema.slope_200}{/if}
                    </span>
                  </div>
                {/if}
                {#if ind.range_state}
                  <div class="ind-row">
                    <span class="ind-label">State</span>
                    <span>
                      {ind.range_state.label} · {ind.range_state.bars} bars
                      {#if ind.range_state.range_low !== null && ind.range_state.range_high !== null}
                        · {fmtPrice(ind.range_state.range_low)}–{fmtPrice(ind.range_state.range_high)}
                        {#if ind.range_state.pos !== null}· {fmtPct(ind.range_state.pos)}{/if}
                      {/if}
                    </span>
                  </div>
                {/if}
                {#if ind.monday}
                  <div class="ind-row">
                    <span class="ind-label">Monday</span>
                    <span>
                      {ind.monday.state}{#if ind.monday.pos !== null}&nbsp;({fmtPct(ind.monday.pos)}){/if}
                    </span>
                  </div>
                {/if}
                {#if ind.candles}
                  <div class="ind-row">
                    <span class="ind-label">Candle</span>
                    <span>
                      {ind.candles.length
                        ? ind.candles.map((c) => `${c.pattern}·${c.direction}`).join(", ")
                        : "none"}
                    </span>
                  </div>
                {/if}
                {#if ind.pa}
                  <div class="ind-row">
                    <span class="ind-label">PA</span>
                    <span>
                      {ind.pa.label} · ER {ind.pa.er.toFixed(2)} · {ind.pa.speed_atr.toFixed(2)} ATR/bar
                    </span>
                  </div>
                {/if}
                {#if ind.bb || ind.vwap}
                  <div class="ind-row">
                    <span class="ind-label">BB/VWAP</span>
                    <span>
                      {#if ind.bb}
                        %B {ind.bb.pct_b.toFixed(2)} · bw {(ind.bb.bandwidth * 100).toFixed(1)}%
                        {#if ind.bb.bw_pctile !== null}
                          (p{Math.round(ind.bb.bw_pctile * 100)}{ind.bb.squeeze ? " squeeze" : ""})
                        {/if}
                      {/if}
                      {#if ind.vwap}
                        {#if ind.bb}·{/if}
                        {#if ind.vwap.weekly_dist_atr !== null}W {fmtDist(ind.vwap.weekly_dist_atr)}{/if}
                        {#if ind.vwap.monthly_dist_atr !== null}M {fmtDist(ind.vwap.monthly_dist_atr)}{/if}
                      {/if}
                    </span>
                  </div>
                {/if}
                {#if ind.profile}
                  <div class="ind-row">
                    <span class="ind-label">VP 60d</span>
                    <span>
                      POC {fmtPrice(ind.profile.poc)} ({fmtDist(ind.profile.poc_dist_atr)})
                      · VA {fmtPrice(ind.profile.val)}–{fmtPrice(ind.profile.vah)}
                      · {ind.profile.vs_value}
                    </span>
                  </div>
                {/if}
              </div>
            {/if}

            {#if panel.sessions}
              {@const s = panel.sessions}
              <div class="sessions muted">
                {#if s.recap}
                  {#each s.recap as row}
                    <div>
                      <span class="sess-name">{row.session}</span>
                      {mytDow(row.start_ms)} {mytHH(row.start_ms)}–{mytHH(row.end_ms)} MYT
                      {#if row.n_bars < row.expected_bars}({row.n_bars}/{row.expected_bars} bars){/if}
                      · net {row.net_pct >= 0 ? "+" : ""}{row.net_pct.toFixed(2)}%{row.net_atr !== null
                        ? ` (${row.net_atr >= 0 ? "+" : ""}${row.net_atr.toFixed(2)} ATR)`
                        : ""} · {row.range_atr !== null ? `range ${row.range_atr.toFixed(1)} ATR` : "range n/a"}
                      {#if row.made_set_high}<span class="sess-mark">·set-high</span>{/if}
                      {#if row.made_set_low}<span class="sess-mark">·set-low</span>{/if}
                    </div>
                  {/each}
                {/if}
                {#if s.tendency}
                  <div>
                    tendency: day-high {s.tendency.map((t) => `${t.session} ${Math.round(t.high_pct * 100)}%`).join(" · ")}
                    | day-low {s.tendency.map((t) => `${t.session} ${Math.round(t.low_pct * 100)}%`).join(" · ")}
                  </div>
                {/if}
              </div>
            {/if}

            <div class="ladder">
              {#each [...panel.levels_above].reverse() as lvl (lvl.name)}
                <div class="lvl-row above">
                  <span class="lvl-name">{lvl.name}</span>
                  <span class="lvl-price num">{fmtPrice(lvl.price)}</span>
                  <span class="lvl-dist num muted">
                    {fmtDist(lvl.dist_atr)}
                    {#if lvl.swept}<span class="swept-tag">swept</span>{/if}
                  </span>
                </div>
              {/each}
              <div class="lvl-row close-row">
                <span class="lvl-name">LAST</span>
                <span class="lvl-price num">{fmtPrice(panel.ref_close)}</span>
                <span class="lvl-dist num muted">
                  {panel.ref_price_source === "1h"
                    ? "1h close"
                    : panel.ref_price_source === "1d_forming"
                      ? "1d forming"
                      : "1d close"}
                </span>
              </div>
              {#each panel.levels_below as lvl (lvl.name)}
                <div class="lvl-row below">
                  <span class="lvl-name">{lvl.name}</span>
                  <span class="lvl-price num">{fmtPrice(lvl.price)}</span>
                  <span class="lvl-dist num muted">
                    {fmtDist(lvl.dist_atr)}
                    {#if lvl.swept}<span class="swept-tag">swept</span>{/if}
                  </span>
                </div>
              {/each}
              {#if panel.levels_above.length === 0 && panel.levels_below.length === 0}
                <div class="lvl-row-empty muted">no reference levels in range</div>
              {/if}
            </div>

            <div class="zones-row">
              {#each [...panel.zones_above, ...panel.zones_below] as z}
                <span class="zone-chip zone-{z.direction}">
                  {z.tf} {z.zone_type.toUpperCase()}
                  {z.zone_low === z.zone_high
                    ? fmtPrice(z.zone_low)
                    : `${fmtPrice(z.zone_low)}–${fmtPrice(z.zone_high)}`}
                  <span class="zone-dist">{z.inside ? "inside" : fmtDist(z.dist_atr)}</span>
                </span>
              {:else}
                <span class="muted">no active zones</span>
              {/each}
            </div>

            {#if panel.seasonality}
              {@const s = panel.seasonality}
              <div class="seasonality-strip muted">
                <span class="dow-tag">{s.dow}</span>
                {#if s.bull_pct !== null}bull {fmtPct(s.bull_pct)}{/if}
                {#if s.avg_range_pct !== null}
                  · range {(s.avg_range_pct * 100).toFixed(1)}%
                  {#if s.sample_days !== null}(n={s.sample_days}){/if}
                {/if}
                {#if s.high_session}
                  · hi {s.high_session}{#if s.low_session} / lo {s.low_session}{/if}
                {/if}
                {#if s.typical_low_day}
                  · wk low {s.typical_low_day}{#if s.typical_high_day} / hi {s.typical_high_day}{/if}
                {/if}
              </div>
            {/if}
          {/if}
        </div>
      {/each}
    </div>

    <!-- Pundit board -->
    <div class="card card-wide pundit-card">
      <div class="card-header">
        <span class="card-title">Pundit Board</span>
        <span class="muted">
          {data.pundit.ledger_total} calls tracked
          {#if data.pundit.ledger_skipped}· {data.pundit.ledger_skipped} skipped{/if}
        </span>
      </div>

      {#if data.pundit.priors_status !== "ok"}
        <div class="pundit-notice">
          priors {data.pundit.priors_status} — run <code>make buibui-pundit-score</code>
        </div>
      {:else if data.pundit.priors_age_days !== null && data.pundit.priors_age_days > 3}
        <div class="pundit-notice">
          priors {data.pundit.priors_age_days}d stale — consider re-running the scorer
        </div>
      {/if}

      <div class="pundit-calls">
        {#each data.pundit.recent_calls as call}
          <div class="call-row">
            <span class="call-dot" class:on-panel={call.on_panel}>{call.on_panel ? "●" : "·"}</span>
            <span class="call-author">{call.author}</span>
            {#if call.prior}
              <span class="prior-badge" class:flagged={call.prior.flagged}>
                {call.prior.flagged
                  ? `⚠ n=${call.prior.n}`
                  : `n=${call.prior.n}${call.prior.hit_rate !== null ? ` · ${fmtPct(call.prior.hit_rate)}` : ""}`}
              </span>
            {/if}
            <span class="call-symbol">{call.symbol}</span>
            <span class="call-dir" class:long={call.direction === "long"} class:short={call.direction === "short"}>
              {call.direction}
            </span>
            <span class="call-entry">&quot;{call.entry}&quot;</span>
            {#if call.target}
              <span class="call-arrow">→</span>
              <span class="call-target">&quot;{call.target}&quot;</span>
            {/if}
            <span class="call-age muted">{call.age_days}d</span>
          </div>
        {:else}
          <div class="muted">no recent calls</div>
        {/each}
      </div>

      <div class="pundit-tables">
        <div class="pundit-table-block">
          <div class="block-title">By author</div>
          <table class="brief-table">
            <thead>
              <tr><th>author</th><th>n</th><th>hit</th><th>avg R</th><th>ATR-R</th></tr>
            </thead>
            <tbody>
              {#each data.pundit.authors as a}
                <tr class:flagged-row={a.flagged}>
                  <td>{a.author}</td>
                  <td class="num muted">{a.n}</td>
                  <td class="num">{fmtPct(a.hit_rate)}</td>
                  <td class="num" class:pos={(a.avg_r ?? 0) > 0} class:neg={(a.avg_r ?? 0) < 0}>
                    {fmtR(a.avg_r)}
                  </td>
                  <td class="num muted">{fmtR(a.avg_atr_r)}</td>
                </tr>
              {:else}
                <tr><td colspan="5" class="muted">no authors scored yet</td></tr>
              {/each}
            </tbody>
          </table>
        </div>

        <div class="pundit-table-block">
          <div class="block-title">By family</div>
          <table class="brief-table">
            <thead>
              <tr><th>family</th><th>dir</th><th>n</th><th>hit</th><th>avg R</th><th>ATR-R</th></tr>
            </thead>
            <tbody>
              {#each data.pundit.families as f}
                <tr class:flagged-row={f.flagged}>
                  <td>{f.family}</td>
                  <td class:long={f.direction === "long"} class:short={f.direction === "short"}>
                    {f.direction}
                  </td>
                  <td class="num muted">{f.n}</td>
                  <td class="num">{fmtPct(f.hit_rate)}</td>
                  <td class="num" class:pos={(f.avg_r ?? 0) > 0} class:neg={(f.avg_r ?? 0) < 0}>
                    {fmtR(f.avg_r)}
                  </td>
                  <td class="num muted">{fmtR(f.avg_atr_r)}</td>
                </tr>
              {:else}
                <tr><td colspan="6" class="muted">no families scored yet</td></tr>
              {/each}
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Health -->
    <div class="card card-wide health-card">
      <div class="card-header">
        <span class="card-title">Data Health</span>
      </div>
      <div class="health-chips">
        {#each data.health.rows as row}
          <span class="health-chip status-{row.status}">
            {row.symbol}/{row.tf}
            {row.status === "ok" ? "✓" : `${row.status} (${row.bars_behind}b)`}
          </span>
        {/each}
      </div>
      {#each data.health.notes as note}
        <div class="health-note muted">note: {note}</div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .page-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
  }

  .controls {
    display: flex;
    align-items: baseline;
    gap: 12px;
    flex-wrap: wrap;
    font-size: 12px;
  }

  .day-ahead {
    color: var(--text);
    font-weight: 600;
    letter-spacing: 0.04em;
  }

  .health-pill {
    font-size: 10px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 2px 8px;
    border-radius: 3px;
    border: 1px solid var(--border);
  }

  .health-pill.ok { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 40%, transparent); }
  .health-pill.warn { color: var(--yellow); border-color: color-mix(in srgb, var(--yellow) 40%, transparent); }

  .clock-pill {
    font-size: 10px;
    letter-spacing: 0.03em;
    padding: 2px 8px;
    border-radius: 3px;
    border: 1px solid var(--border);
    color: var(--text-dim);
  }

  .btn-refresh {
    font-size: 11px;
    padding: 5px 14px;
  }

  /* ── Grid / cards ──────────────────────────────────────────────────────── */
  .grid {
    display: grid;
    gap: 16px;
    margin-top: 16px;
  }

  .panels-grid {
    grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  }

  .card {
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 16px;
  }

  .card-wide { margin-top: 16px; }

  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 10px;
  }

  .card-title {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--accent);
  }

  .muted { color: var(--muted); }
  .pos { color: var(--green); }
  .neg { color: var(--red); }
  .long { color: var(--green); }
  .short { color: var(--red); }

  /* ── Regime chips ──────────────────────────────────────────────────────── */
  .regime-chips { display: flex; gap: 6px; }

  .regime-chip {
    font-size: 9px;
    letter-spacing: 0.05em;
    padding: 2px 6px;
    border-radius: 3px;
    border: 1px solid var(--border);
    color: var(--text-dim);
  }

  .regime-chip.regime-trend { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 35%, transparent); }
  .regime-chip.regime-high_vol { color: var(--yellow); border-color: color-mix(in srgb, var(--yellow) 35%, transparent); }
  .regime-chip.regime-range { color: var(--text-dim); }
  .regime-chip.regime-unknown { color: var(--muted); }

  .panel-meta {
    font-size: 11px;
    margin-bottom: 10px;
  }

  /* ── Price ladder (the page's signature element) ──────────────────────── */
  .ladder {
    display: grid;
    gap: 1px;
    font-size: 12px;
    margin-bottom: 10px;
    font-variant-numeric: tabular-nums;
  }

  .lvl-row {
    display: grid;
    grid-template-columns: 56px 1fr auto;
    gap: 8px;
    padding: 2px 4px;
    border-radius: 2px;
  }

  .lvl-row-empty {
    font-size: 11px;
    padding: 4px;
  }

  .lvl-name {
    font-size: 10px;
    letter-spacing: 0.04em;
    color: var(--text-dim);
    align-self: center;
  }

  .lvl-price {
    text-align: right;
    color: var(--text);
  }

  .lvl-dist {
    text-align: right;
    font-size: 10px;
  }

  .close-row {
    background: color-mix(in srgb, var(--accent) 10%, transparent);
    border-top: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
    border-bottom: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
    margin: 2px 0;
  }

  .close-row .lvl-name,
  .close-row .lvl-price {
    color: var(--accent);
    font-weight: 700;
  }

  .swept-tag {
    display: inline-block;
    margin-left: 4px;
    font-size: 8px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--accent);
    background: var(--accent-dim);
    border-radius: 2px;
    padding: 1px 4px;
  }

  /* ── Zones ─────────────────────────────────────────────────────────────── */
  .zones-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 8px;
    font-size: 10px;
  }

  .zone-chip {
    padding: 2px 6px;
    border-radius: 3px;
    background: var(--bg);
    border-left: 2px solid var(--border);
    color: var(--text-dim);
  }

  .zone-chip.zone-bull { border-left-color: var(--green); }
  .zone-chip.zone-bear { border-left-color: var(--red); }

  .zone-dist {
    color: var(--muted);
    margin-left: 4px;
  }

  /* ── Seasonality strip ─────────────────────────────────────────────────── */
  .seasonality-strip {
    font-size: 11px;
    line-height: 1.6;
  }

  .dow-tag {
    color: var(--accent);
    font-weight: 700;
    margin-right: 2px;
  }

  /* ── Pundit board ──────────────────────────────────────────────────────── */
  .pundit-notice {
    font-size: 11px;
    color: var(--yellow);
    background: color-mix(in srgb, var(--yellow) 8%, transparent);
    border: 1px solid color-mix(in srgb, var(--yellow) 25%, transparent);
    border-radius: 3px;
    padding: 6px 10px;
    margin-bottom: 10px;
  }

  .pundit-notice code {
    font-family: var(--font-mono);
    color: var(--text);
  }

  .pundit-calls {
    display: flex;
    flex-direction: column;
    gap: 6px;
    max-height: 260px;
    overflow-y: auto;
    margin-bottom: 14px;
    padding-right: 4px;
  }

  .call-row {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 6px;
    font-size: 12px;
    padding: 3px 0;
    border-bottom: 1px solid var(--border-dim);
  }

  .call-dot { color: var(--muted); }
  .call-dot.on-panel { color: var(--accent); }

  .call-author { color: var(--text); font-weight: 600; }

  .prior-badge {
    font-size: 9px;
    letter-spacing: 0.03em;
    padding: 1px 5px;
    border-radius: 3px;
    color: var(--accent);
    background: var(--accent-dim);
  }

  .prior-badge.flagged {
    color: var(--yellow);
    background: color-mix(in srgb, var(--yellow) 12%, transparent);
  }

  .call-symbol { color: var(--text-dim); }

  .call-dir {
    font-size: 10px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  .call-entry, .call-target { color: var(--text-dim); }
  .call-arrow { color: var(--muted); }
  .call-age { font-size: 10px; margin-left: auto; }

  .pundit-tables {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
  }

  .block-title {
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 6px;
  }

  .brief-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 11.5px;
  }

  .brief-table th {
    text-align: left;
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    padding: 4px 6px;
  }

  .brief-table td {
    padding: 4px 6px;
    border-bottom: 1px solid var(--border-dim);
    color: var(--text);
  }

  .brief-table tr:last-child td { border-bottom: none; }

  .flagged-row td:first-child {
    box-shadow: inset 2px 0 0 var(--yellow);
  }

  /* ── Health footer ─────────────────────────────────────────────────────── */
  .health-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .health-chip {
    font-size: 10px;
    letter-spacing: 0.03em;
    padding: 2px 7px;
    border-radius: 3px;
    border: 1px solid var(--border);
    color: var(--text-dim);
  }

  .health-chip.status-ok { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 35%, transparent); }
  .health-chip.status-stale { color: var(--yellow); border-color: color-mix(in srgb, var(--yellow) 35%, transparent); }
  .health-chip.status-missing { color: var(--red); border-color: color-mix(in srgb, var(--red) 35%, transparent); }

  .health-note {
    font-size: 11px;
    margin-top: 6px;
  }

  .legend-card { margin-bottom: 16px; }
  .legend-list { display: grid; grid-template-columns: max-content 1fr; gap: 4px 14px; font-size: 0.85rem; margin: 0; }
  .legend-list dt { font-weight: 600; color: var(--text-dim); }
  .legend-list dd { margin: 0; }

  /* ── Indicator states ─────────────────────────────────────────────────── */
  .indicators {
    display: grid;
    gap: 2px;
    font-size: 11px;
    margin-bottom: 10px;
    font-variant-numeric: tabular-nums;
  }

  .ind-row {
    display: grid;
    grid-template-columns: 56px 1fr;
    gap: 8px;
    padding: 1px 4px;
  }

  .ind-label {
    font-size: 10px;
    letter-spacing: 0.04em;
    color: var(--text-dim);
  }

  .ema-bit { margin-right: 4px; }

  /* ── Sessions ──────────────────────────────────────────────────────────── */
  .sessions {
    display: grid;
    gap: 2px;
    font-size: 11px;
    margin-bottom: 10px;
    font-variant-numeric: tabular-nums;
  }

  .sess-name {
    display: inline-block;
    min-width: 48px;
    color: var(--text-dim);
  }

  .sess-mark {
    color: var(--accent);
    margin-left: 2px;
  }
</style>
