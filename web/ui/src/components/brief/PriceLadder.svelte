<script lang="ts">
  import type { BriefLevelRow } from "../../api";
  import { fmtDist, fmtPrice, proximity } from "./format";

  // The page's signature element: reference levels as an actual ladder around
  // the last price, rather than two lists that happen to be printed in order.
  //
  // The gauge encodes proximity, not distance — a level 0.1 ATR away gets a
  // full-width bar and one 3 ATR away gets none. That is the direction that
  // matches how the ladder is read: what is close is what is about to matter.
  // Distance is still printed, so the gauge adds a scannable channel without
  // becoming the only way to read the number.
  //
  // above/below were already in the markup as classes before this component
  // existed, but no rule ever matched them, so resistance and support rendered
  // identically. They are now the one place this card spends colour.

  type Props = {
    levelsAbove: BriefLevelRow[];
    levelsBelow: BriefLevelRow[];
    refClose: number;
    refPriceSource: string;
  };

  let { levelsAbove, levelsBelow, refClose, refPriceSource }: Props = $props();

  const sourceLabel = (s: string): string =>
    s === "1h" ? "1h close" : s === "1d_forming" ? "1d forming" : "1d close";

  // Nearest-first away from the price line: above ascends, so it is reversed to
  // put the nearest level adjacent to LAST.
  let above = $derived([...levelsAbove].reverse());
  let empty = $derived(levelsAbove.length === 0 && levelsBelow.length === 0);
</script>

<div class="ladder">
  {#each above as lvl (lvl.name)}
    <div class="lvl-row above">
      <span class="lvl-name">{lvl.name}</span>
      <span class="gauge" aria-hidden="true">
        <span class="gauge-fill" style="width: {proximity(lvl.dist_atr) * 100}%"></span>
      </span>
      <span class="lvl-price num">{fmtPrice(lvl.price)}</span>
      <span class="lvl-dist num">
        {fmtDist(lvl.dist_atr)}
        {#if lvl.swept}<span class="swept-tag">swept</span>{/if}
      </span>
    </div>
  {/each}

  <div class="lvl-row close-row">
    <span class="lvl-name">LAST</span>
    <span class="gauge" aria-hidden="true"></span>
    <span class="lvl-price num">{fmtPrice(refClose)}</span>
    <span class="lvl-dist">{sourceLabel(refPriceSource)}</span>
  </div>

  {#each levelsBelow as lvl (lvl.name)}
    <div class="lvl-row below">
      <span class="lvl-name">{lvl.name}</span>
      <span class="gauge" aria-hidden="true">
        <span class="gauge-fill" style="width: {proximity(lvl.dist_atr) * 100}%"></span>
      </span>
      <span class="lvl-price num">{fmtPrice(lvl.price)}</span>
      <span class="lvl-dist num">
        {fmtDist(lvl.dist_atr)}
        {#if lvl.swept}<span class="swept-tag">swept</span>{/if}
      </span>
    </div>
  {/each}

  {#if empty}
    <div class="lvl-row-empty">no reference levels in range</div>
  {/if}
</div>

<style>
  .ladder {
    display: grid;
    gap: 1px;
    font-size: var(--fs-data);
    font-variant-numeric: tabular-nums;
  }

  .lvl-row {
    display: grid;
    grid-template-columns: 46px 1fr auto 62px;
    align-items: center;
    gap: 8px;
    padding: 2px 4px;
    border-radius: 2px;
  }

  .lvl-name {
    font-size: var(--fs-micro);
    letter-spacing: 0.04em;
    color: var(--text-soft);
  }

  .lvl-price {
    text-align: right;
    color: var(--text);
  }

  .lvl-dist {
    text-align: right;
    font-size: var(--fs-micro);
    color: var(--text-soft);
  }

  /* The gauge: grows from the price column outward, so the bars form a
     silhouette that narrows away from LAST. */
  .gauge {
    display: flex;
    justify-content: flex-end;
    height: 3px;
    min-width: 24px;
    border-radius: 2px;
    background: color-mix(in srgb, var(--border) 45%, transparent);
    overflow: hidden;
  }

  .gauge-fill {
    height: 100%;
    border-radius: 2px;
    transition: width 160ms ease-out;
  }

  .above .gauge-fill {
    background: color-mix(in srgb, var(--red) 62%, transparent);
  }

  .below .gauge-fill {
    background: color-mix(in srgb, var(--green) 62%, transparent);
  }

  .close-row {
    background: color-mix(in srgb, var(--accent) 10%, transparent);
    border-top: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
    border-bottom: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
    margin: 3px 0;
    padding-top: 3px;
    padding-bottom: 3px;
  }

  .close-row .lvl-name,
  .close-row .lvl-price {
    color: var(--accent);
    font-weight: 700;
  }

  .close-row .lvl-price {
    font-size: var(--fs-lead);
  }

  .close-row .gauge {
    background: none;
  }

  .lvl-row-empty {
    font-size: var(--fs-annot);
    color: var(--text-soft);
    padding: 4px;
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

  @media (prefers-reduced-motion: reduce) {
    .gauge-fill { transition: none; }
  }
</style>
