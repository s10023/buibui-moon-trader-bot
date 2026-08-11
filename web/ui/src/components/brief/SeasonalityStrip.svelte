<script lang="ts">
  import type { BriefSeasonalityStrip } from "../../api";
  import { fmtPct } from "./format";

  type Props = { seasonality: BriefSeasonalityStrip };
  let { seasonality: s }: Props = $props();
</script>

<div class="strip">
  <span class="dow">{s.dow}</span>
  {#if s.bull_pct !== null}
    <span class="stat"><span class="k">bull</span> {fmtPct(s.bull_pct)}</span>
  {/if}
  {#if s.avg_range_pct !== null}
    <!-- Mean and median together: daily range is right-skewed, so a mean far
         above the median means the day's reputation rests on outliers. -->
    <span class="stat">
      <span class="k">range</span> {(s.avg_range_pct * 100).toFixed(1)}%<span class="unit"
        >avg</span
      >
      {#if s.median_range_pct !== null}
        <span class="slash">/</span>{(s.median_range_pct * 100).toFixed(1)}%<span class="unit"
          >med</span
        >
      {/if}
      {#if s.sample_days !== null}<span class="n">n={s.sample_days}</span>{/if}
    </span>
  {/if}
  {#if s.high_session}
    <span class="stat">
      <span class="k">hi</span> {s.high_session}{#if s.low_session}
        <span class="k">lo</span> {s.low_session}{/if}
    </span>
  {/if}
  {#if s.typical_low_day}
    <span class="stat">
      <span class="k">wk lo</span> {s.typical_low_day}{#if s.typical_high_day}
        <span class="k">hi</span> {s.typical_high_day}{/if}
    </span>
  {/if}
</div>

<style>
  .strip {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 4px 12px;
    font-size: var(--fs-micro);
    font-variant-numeric: tabular-nums;
  }

  .dow {
    color: var(--accent);
    font-weight: 700;
    letter-spacing: 0.06em;
  }

  .stat { color: var(--text-dim); }

  /* Key/value pairs rather than a "·"-joined run — the separators were doing
     the work that a label should do. */
  .k {
    color: var(--muted);
    letter-spacing: 0.05em;
    margin-right: 2px;
  }

  .n { color: var(--muted); margin-left: 3px; }
  .unit { color: var(--muted); margin-left: 2px; font-size: 9px; }
  .slash { color: var(--muted); margin: 0 2px; }
</style>
