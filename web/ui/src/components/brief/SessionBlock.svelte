<script lang="ts">
  import type { BriefSessionState } from "../../api";
  import { mytDow, mytHH, signed } from "./format";

  type Props = { sessions: BriefSessionState };
  let { sessions }: Props = $props();
</script>

{#if sessions.recap}
  <div class="recap">
    {#each sessions.recap as row}
      <div class="sess">
        <span class="sess-name">{row.session}</span>
        <span class="sess-when">
          {mytDow(row.start_ms)} {mytHH(row.start_ms)}–{mytHH(row.end_ms)}
          {#if row.n_bars < row.expected_bars}
            <span class="partial">{row.n_bars}/{row.expected_bars}b</span>
          {/if}
        </span>
        <span class="sess-net num" class:pos={row.net_pct > 0} class:neg={row.net_pct < 0}>
          {signed(row.net_pct, 2)}%{row.net_atr !== null
            ? ` (${signed(row.net_atr, 2)} ATR)`
            : ""}
        </span>
        <span class="sess-range num">
          {row.range_atr !== null ? `${row.range_atr.toFixed(1)} ATR` : "n/a"}
        </span>
        <span class="sess-marks">
          {#if row.made_set_high}<span class="mark">set-high</span>{/if}
          {#if row.made_set_low}<span class="mark">set-low</span>{/if}
        </span>
      </div>
    {/each}
  </div>
{/if}

{#if sessions.tendency}
  <div class="tendency">
    <span class="t-label">day-high</span>
    {sessions.tendency.map((t) => `${t.session} ${Math.round(t.high_pct * 100)}%`).join(" · ")}
    <span class="t-label">day-low</span>
    {sessions.tendency.map((t) => `${t.session} ${Math.round(t.low_pct * 100)}%`).join(" · ")}
  </div>
{/if}

<style>
  .recap {
    display: grid;
    gap: 2px;
    font-variant-numeric: tabular-nums;
  }

  /* Columns, not a run-on sentence. The old markup concatenated name, window,
     net, range and markers into one wrapping line per session, so comparing
     three sessions meant reading three paragraphs instead of scanning a column. */
  .sess {
    display: grid;
    grid-template-columns: 46px 1fr auto auto minmax(0, auto);
    align-items: baseline;
    gap: 8px;
  }

  .sess-name {
    font-size: var(--fs-micro);
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--text-soft);
  }

  .sess-when { color: var(--muted); font-size: var(--fs-micro); }
  .partial { color: var(--yellow); }
  .sess-net { text-align: right; color: var(--text); }
  .sess-range { text-align: right; color: var(--text-soft); font-size: var(--fs-micro); }

  .mark {
    font-size: 8px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--accent);
    background: var(--accent-dim);
    border-radius: 2px;
    padding: 1px 4px;
    margin-left: 3px;
  }

  .tendency {
    margin-top: 5px;
    color: var(--text-soft);
    font-size: var(--fs-micro);
  }

  .t-label {
    color: var(--muted);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin-right: 4px;
  }

  .t-label:not(:first-child) { margin-left: 10px; }

  .pos { color: var(--green); }
  .neg { color: var(--red); }
</style>
