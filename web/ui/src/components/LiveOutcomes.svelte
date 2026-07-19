<script lang="ts">
  import { onMount } from "svelte";
  import { getLiveOutcomes, type LiveOutcomesResponse } from "../api";

  let liveOutcomes = $state<LiveOutcomesResponse | null>(null);
  let loLoading = $state(false);
  let loError = $state<string | null>(null);
  let loDays = $state(30); // 0 = all time
  let loMinN = $state(1);

  onMount(() => {
    void loadLiveOutcomes();
  });

  const formatPct = (v: number) => (v * 100).toFixed(1) + "%";

  async function loadLiveOutcomes(): Promise<void> {
    loLoading = true;
    loError = null;
    try {
      liveOutcomes = await getLiveOutcomes(loDays, loMinN);
    } catch (e) {
      loError = e instanceof Error ? e.message : String(e);
    } finally {
      loLoading = false;
    }
  }

  function setLoDays(d: number): void {
    if (loDays === d) return;
    loDays = d;
    void loadLiveOutcomes();
  }

  function setLoMinN(n: number): void {
    if (loMinN === n) return;
    loMinN = n;
    void loadLiveOutcomes();
  }

  const fmtR = (v: number | null) => (v === null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(3));
  // Largest |avg_r| among visible cells — used to scale the diverging bars.
  const loMaxAbsR = $derived(
    liveOutcomes
      ? Math.max(
          0.01,
          ...liveOutcomes.cells.map((c) => Math.abs(c.avg_r ?? 0)),
          ...liveOutcomes.by_strategy.map((s) => Math.abs(s.avg_r ?? 0)),
        )
      : 1
  );
</script>

{#snippet rbar(v: number | null)}
  <span class="lo-bar">
    <span class="lo-bar-mid"></span>
    {#if v !== null && v !== 0}
      {@const w = Math.min(Math.abs(v) / loMaxAbsR, 1) * 50}
      <span
        class="lo-bar-fill"
        class:pos={v > 0}
        class:neg={v < 0}
        style="width:{w.toFixed(1)}%; {v > 0 ? 'left:50%' : 'right:50%'}"
      ></span>
    {/if}
  </span>
{/snippet}

<div class="lo-controls">
  <div class="lo-chip-group">
    <button class="lo-chip" class:active={loDays === 30} onclick={() => setLoDays(30)}>30D</button>
    <button class="lo-chip" class:active={loDays === 90} onclick={() => setLoDays(90)}>90D</button>
    <button class="lo-chip" class:active={loDays === 0} onclick={() => setLoDays(0)}>All</button>
  </div>
  <div class="lo-chip-group">
    <button class="lo-chip" class:active={loMinN === 1} onclick={() => setLoMinN(1)}>n≥1</button>
    <button class="lo-chip" class:active={loMinN === 10} onclick={() => setLoMinN(10)}>n≥10</button>
  </div>
</div>

{#if loError}
  <div class="lo-msg val-red">Failed to load outcomes: {loError}</div>
{:else if !liveOutcomes}
  <div class="lo-msg muted">Loading live outcomes…</div>
{:else if liveOutcomes.rollup.total_rows === 0}
  <div class="lo-msg muted">No alerts fired yet — the ledger is empty.</div>
{:else}
  {@const rollup = liveOutcomes.rollup}
  <div class="lo-rollup">
    <div class="lo-stat">
      <span class="lo-stat-val">{rollup.total_rows.toLocaleString()}</span>
      <span class="lo-stat-label">fired</span>
    </div>
    <div class="lo-stat">
      <span class="lo-stat-val">{rollup.resolved.toLocaleString()}</span>
      <span class="lo-stat-label">resolved</span>
    </div>
    <div class="lo-stat">
      <span class="lo-stat-val">{rollup.open.toLocaleString()}</span>
      <span class="lo-stat-label">open</span>
    </div>
    <div class="lo-stat lo-stat-integrity" class:val-green={rollup.open_no_tp === 0} class:val-red={rollup.open_no_tp > 0}>
      <span class="lo-stat-val">{rollup.open_no_tp === 0 ? "✓ 0" : rollup.open_no_tp.toLocaleString()}</span>
      <span class="lo-stat-label">no-TP hole</span>
    </div>
    <div class="lo-wle">
      <span class="val-green">{rollup.wins.toLocaleString()} W</span>
      <span class="lo-sep">·</span>
      <span class="val-red">{rollup.losses.toLocaleString()} L</span>
      <span class="lo-sep">·</span>
      <span class="muted">{rollup.expired.toLocaleString()} exp</span>
    </div>
  </div>
  <div class="lo-scope muted">
    all-time roll-up · tables show {loDays === 0 ? "all time" : `last ${loDays}d`}, min n {loMinN}
  </div>

  {#if liveOutcomes.by_strategy.length === 0}
    <div class="lo-msg muted">No resolved trades in this window — widen the period or lower min n.</div>
  {:else}
    <div class="lo-cols">
      <div class="lo-block">
        <div class="lo-block-title">By strategy</div>
        <div class="lo-table">
          <div class="lo-row lo-head">
            <span>strategy</span><span class="num">n</span><span class="num">win</span><span class="num">avg R</span><span></span>
          </div>
          {#each liveOutcomes.by_strategy as s}
            <div class="lo-row">
              <span class="lo-strat">{s.strategy}</span>
              <span class="num muted">{s.n}</span>
              <span class="num">{s.win_rate === null ? "—" : formatPct(s.win_rate)}</span>
              <span class="num" class:val-green={(s.avg_r ?? 0) > 0} class:val-red={(s.avg_r ?? 0) < 0}>{fmtR(s.avg_r)}</span>
              <span class="lo-bar-cell">{@render rbar(s.avg_r)}</span>
            </div>
          {/each}
        </div>
      </div>

      <div class="lo-block">
        <div class="lo-block-title">By strategy · tf · direction</div>
        <div class="lo-table lo-scroll">
          <div class="lo-row lo-cell-row lo-head">
            <span>strat</span><span>tf</span><span>dir</span><span class="num">n</span><span class="num">win</span><span class="num">avg R</span><span></span>
          </div>
          {#each liveOutcomes.cells as c}
            <div class="lo-row lo-cell-row">
              <span class="lo-strat">{c.strategy}</span>
              <span class="muted">{c.tf}</span>
              <span class:val-green={c.direction === "long"} class:val-red={c.direction === "short"}>{c.direction === "long" ? "▲ L" : "▼ S"}</span>
              <span class="num muted">{c.n}</span>
              <span class="num">{c.win_rate === null ? "—" : formatPct(c.win_rate)}</span>
              <span class="num" class:val-green={(c.avg_r ?? 0) > 0} class:val-red={(c.avg_r ?? 0) < 0}>{fmtR(c.avg_r)}</span>
              <span class="lo-bar-cell">{@render rbar(c.avg_r)}</span>
            </div>
          {/each}
        </div>
      </div>
    </div>
  {/if}
{/if}

<style>
  /* Chrome colours — Stats.svelte defines these too (Svelte scopes styles per
     component, so the parent's rule doesn't reach here); keep in sync. */
  .val-green { color: var(--green, #4caf81); }
  .val-red { color: var(--red, #e05c5c); }

  /* Control row — relocated from the card header (was `.pill-toggle`); styled
     to match PathCone.svelte's `.chip-group` / `.chip`. */
  .lo-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin: 10px 0 4px;
  }
  .lo-chip-group {
    display: flex;
    gap: 4px;
  }
  .lo-chip {
    background: transparent;
    border: 1px solid #333;
    color: #aaa;
    border-radius: 4px;
    padding: 0.15rem 0.55rem;
    font-size: 0.78rem;
    cursor: pointer;
  }
  .lo-chip.active {
    border-color: #60a5fa;
    color: #e5e7eb;
  }

  .lo-msg {
    font-size: 12px;
    padding: 10px 2px;
  }

  /* Roll-up chips */
  .lo-rollup {
    display: flex;
    align-items: stretch;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 6px;
  }

  .lo-stat {
    display: flex;
    flex-direction: column;
    gap: 1px;
    padding: 7px 14px;
    border: 1px solid var(--border);
    border-radius: 5px;
    background: color-mix(in srgb, var(--accent) 4%, transparent);
    min-width: 72px;
  }

  .lo-stat-val {
    font-size: 18px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    line-height: 1.1;
  }

  .lo-stat-label {
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 0.07em;
    text-transform: uppercase;
    color: var(--muted);
  }

  .lo-stat-integrity {
    background: color-mix(in srgb, currentColor 8%, transparent);
    border-color: color-mix(in srgb, currentColor 30%, var(--border));
  }

  .lo-wle {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-left: auto;
    padding: 7px 4px;
    font-size: 13px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  .lo-wle .lo-sep { color: var(--muted); font-weight: 400; }

  .lo-scope {
    font-size: 10px;
    margin-bottom: 12px;
  }

  /* Two-column block layout */
  .lo-cols {
    display: grid;
    grid-template-columns: minmax(0, 0.85fr) minmax(0, 1.15fr);
    gap: 18px;
  }

  @media (max-width: 760px) {
    .lo-cols { grid-template-columns: 1fr; }
  }

  .lo-block-title {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 6px;
  }

  .lo-table { display: flex; flex-direction: column; }

  .lo-scroll {
    max-height: 360px;
    overflow-y: auto;
  }

  .lo-row {
    display: grid;
    grid-template-columns: 1fr 34px 46px 56px 64px;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    padding: 3px 4px;
    border-radius: 3px;
    margin: 0 -4px;
  }

  .lo-cell-row {
    grid-template-columns: 1fr 34px 34px 30px 44px 56px 60px;
  }

  .lo-row:not(.lo-head):hover {
    background: color-mix(in srgb, var(--accent) 6%, transparent);
  }

  .lo-head {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    padding-bottom: 5px;
    margin-bottom: 2px;
    position: sticky;
    top: 0;
    background: var(--bg-panel);
    z-index: 1;
  }

  .lo-row .num { text-align: right; }
  .lo-strat {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* Diverging avg-R bar (red left / green right of centre) */
  .lo-bar-cell { display: flex; }
  .lo-bar {
    position: relative;
    width: 100%;
    height: 8px;
    background: color-mix(in srgb, var(--border) 50%, transparent);
    border-radius: 2px;
  }

  .lo-bar-mid {
    position: absolute;
    left: 50%;
    top: -1px;
    bottom: -1px;
    width: 1px;
    background: var(--muted);
    opacity: 0.5;
  }

  .lo-bar-fill {
    position: absolute;
    top: 0;
    bottom: 0;
    border-radius: 2px;
  }

  .lo-bar-fill.pos { background: var(--green, #4caf81); }
  .lo-bar-fill.neg { background: var(--red, #e05c5c); }
</style>
