<script lang="ts">
  import type { BriefMonthlyContext, BriefWeeklyState } from "../../api";
  import { fmtRank, hourTag, signed, weekHourLabel } from "./format";

  // Month and week share a block because they answer the same question at two
  // horizons — where does the forming period sit against its own history.
  // Splitting them into two identically-styled paragraphs is what the old page
  // did, and it read as more of the same grey text.

  type Props = {
    monthly: BriefMonthlyContext | null;
    weekly: BriefWeeklyState | null;
  };

  let { monthly, weekly }: Props = $props();
</script>

{#if monthly}
  {@const m = monthly}
  <div class="period">
    <span class="p-tag">Month</span>
    <span class="p-body">
      <span class="p-lead num" class:pos={m.mtd_return_pct > 0} class:neg={m.mtd_return_pct < 0}
        >{signed(m.mtd_return_pct, 1)}%</span
      >
      <span class="p-rest">
        {m.pct_of_months === null ? "—" : `p${Math.round(m.pct_of_months)}`} of {m.n_months} months
        <span class="sep">·</span> range pos
        {m.range_position === null ? "—" : m.range_position.toFixed(2)}
        <span class="sep">·</span> {Math.round(m.mtd_elapsed_frac * 100)}% elapsed
      </span>
    </span>
  </div>
{/if}

{#if weekly}
  {@const w = weekly}
  <div class="period">
    <span class="p-tag">Week</span>
    <span class="p-body">
      <span class="p-lead num">{signed(w.norm_now, 2)}×AWR</span>
      <span class="p-rest">
        {w.path_direction} path <span class="sep">·</span> h{w.elapsed_h}/{w.total_bars}
        ({weekHourLabel(w)})
      </span>
    </span>
  </div>
  <!--
    The conditional pool falls back to the unconditional one whenever a
    same-direction cohort can't be resolved distinctly — either "flat" has no
    cohort at all, or the bull/bear combo exists but is empty.
    `conditional_is_fallback` covers BOTH; keying on path_direction alone missed
    the empty-combo case and labelled the unconditional population as a cohort.
    So drop the cohort clause entirely and attribute the timing stat to "all
    weeks" instead of "those weeks".
  -->
  <div class="p-sub">
    {#if w.conditional_is_fallback}
      {fmtRank(w.pct_unconditional)} unconditional (n={w.n_unconditional})
    {:else}
      {fmtRank(w.pct_conditional)} of weeks that closed {w.path_direction}
      (n={w.n_conditional}) <span class="sep">·</span>
      {fmtRank(w.pct_unconditional)} unconditional (n={w.n_unconditional})
    {/if}
  </div>
  <div class="p-sub">
    low close {hourTag(w.low_hour)} <span class="sep">·</span> high close {hourTag(w.high_hour)}
    <span class="sep">·</span> {Math.round(w.low_in_by_now * 100)}% of
    {w.conditional_is_fallback ? "all weeks" : "those weeks"} had set their low by now
  </div>
{/if}

<style>
  /* Two columns, not three. With a separate column for the lead value, a
     wrapping third column hung its continuation lines under that column's left
     edge — so "32% elapsed" broke with "elapsed" indented past the number above
     it. Letting lead and rest flow inline in one cell wraps under the value,
     which is where the eye expects it. */
  .period {
    display: grid;
    grid-template-columns: 46px 1fr;
    align-items: baseline;
    gap: 8px;
    font-variant-numeric: tabular-nums;
  }

  .period > .p-body {
    display: block;
  }

  .period + .period { margin-top: 4px; }

  .p-tag {
    font-size: var(--fs-micro);
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--text-soft);
  }

  /* One number per row gets promoted — the thing you actually look for. */
  .p-lead {
    font-size: var(--fs-data);
    font-weight: 600;
    color: var(--text);
    margin-right: 5px;
  }

  .p-rest { color: var(--text-dim); }

  .p-sub {
    margin-top: 3px;
    padding-left: 54px;
    text-indent: -6px;
    color: var(--text-soft);
    font-size: var(--fs-micro);
  }

  .sep { color: var(--muted); }
  .pos { color: var(--green); }
  .neg { color: var(--red); }
</style>
