<script lang="ts">
  import type { BriefIndicatorState } from "../../api";
  import { fmtDist, fmtPct, fmtPrice } from "./format";

  type Props = { ind: BriefIndicatorState };
  let { ind }: Props = $props();

  let emaBits = $derived(
    ind.ema
      ? ([
          [ind.ema.above_20, 20],
          [ind.ema.above_50, 50],
          [ind.ema.above_200, 200],
        ] as [boolean | null, number][])
      : [],
  );
</script>

<dl class="rows">
  {#if ind.ema}
    <dt>EMA</dt>
    <dd>
      {#each emaBits as [above, span]}
        <span class="ema-bit" class:pos={above === true} class:neg={above === false}>
          {above === null ? "—" : above ? "▲" : "▼"}{span}
        </span>
      {/each}
      {#if ind.ema.stack}<span class="sep">·</span> stack {ind.ema.stack}{/if}
      {#if ind.ema.slope_200}<span class="sep">·</span> 200 {ind.ema.slope_200}{/if}
    </dd>
  {/if}

  {#if ind.range_state}
    <dt>State</dt>
    <dd>
      {ind.range_state.label} <span class="sep">·</span> {ind.range_state.bars} bars
      {#if ind.range_state.range_low !== null && ind.range_state.range_high !== null}
        <span class="sep">·</span>
        {fmtPrice(ind.range_state.range_low)}–{fmtPrice(ind.range_state.range_high)}
        {#if ind.range_state.pos !== null}
          <span class="sep">·</span> {fmtPct(ind.range_state.pos)}
        {/if}
      {/if}
    </dd>
  {/if}

  {#if ind.monday}
    <dt>Monday</dt>
    <dd>
      {ind.monday.state}{#if ind.monday.pos !== null}&nbsp;({fmtPct(ind.monday.pos)}){/if}
    </dd>
  {/if}

  {#if ind.candles}
    <dt>Candle</dt>
    <dd>
      {ind.candles.length
        ? ind.candles.map((c) => `${c.pattern}·${c.direction}`).join(", ")
        : "none"}
    </dd>
  {/if}

  {#if ind.pa}
    <dt>PA</dt>
    <dd>
      {ind.pa.label} <span class="sep">·</span> ER {ind.pa.er.toFixed(2)}
      <span class="sep">·</span> {ind.pa.speed_atr.toFixed(2)} ATR/bar
    </dd>
  {/if}

  {#if ind.bb || ind.vwap}
    <dt>BB/VWAP</dt>
    <dd>
      {#if ind.bb}
        %B {ind.bb.pct_b.toFixed(2)} <span class="sep">·</span> bw
        {(ind.bb.bandwidth * 100).toFixed(1)}%
        {#if ind.bb.bw_pctile !== null}
          (p{Math.round(ind.bb.bw_pctile * 100)}{ind.bb.squeeze ? " squeeze" : ""})
        {/if}
      {/if}
      {#if ind.vwap}
        {#if ind.bb}<span class="sep">·</span>{/if}
        {#if ind.vwap.weekly_dist_atr !== null}W {fmtDist(ind.vwap.weekly_dist_atr)}{/if}
        {#if ind.vwap.monthly_dist_atr !== null}M {fmtDist(ind.vwap.monthly_dist_atr)}{/if}
      {/if}
    </dd>
  {/if}

  {#if ind.profile}
    <dt>VP 60d</dt>
    <dd>
      POC {fmtPrice(ind.profile.poc)} ({fmtDist(ind.profile.poc_dist_atr)})
      <span class="sep">·</span> VA {fmtPrice(ind.profile.val)}–{fmtPrice(ind.profile.vah)}
      <span class="sep">·</span> {ind.profile.vs_value}
    </dd>
  {/if}
</dl>

<style>
  /* A definition list rather than div soup: the left column really is a set of
     terms and the right column really is their values, and the grid keeps the
     labels aligned so the eye can run down them. */
  .rows {
    display: grid;
    grid-template-columns: 52px 1fr;
    gap: 3px 8px;
    margin: 0;
    font-variant-numeric: tabular-nums;
  }

  dt {
    font-size: var(--fs-micro);
    letter-spacing: 0.04em;
    color: var(--text-soft);
  }

  dd {
    margin: 0;
    color: var(--text-dim);
  }

  .sep { color: var(--muted); }

  .ema-bit { margin-right: 5px; }
  .pos { color: var(--green); }
  .neg { color: var(--red); }
</style>
