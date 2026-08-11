<script lang="ts">
  import type { BriefSymbolPanel } from "../../api";
  import ErrorBanner from "../ErrorBanner.svelte";
  import Block from "./Block.svelte";
  import ExternalBlock from "./ExternalBlock.svelte";
  import IndicatorBlock from "./IndicatorBlock.svelte";
  import PeriodBlock from "./PeriodBlock.svelte";
  import PriceLadder from "./PriceLadder.svelte";
  import SeasonalityStrip from "./SeasonalityStrip.svelte";
  import SessionBlock from "./SessionBlock.svelte";
  import ZonesRow from "./ZonesRow.svelte";
  import { REGIME_LABEL, fmtPct, fmtPrice } from "./format";
  import { scope } from "./scope.svelte";

  type Props = { panel: BriefSymbolPanel };
  let { panel }: Props = $props();

  let showPeriods = $derived(
    scope.on.periods && (panel.monthly !== null || panel.weekly !== null),
  );
</script>

<article class="panel">
  <header class="head">
    <div class="ident">
      <h3 class="ticker">{panel.symbol}</h3>
      {#if !panel.error}
        <span class="last num">{fmtPrice(panel.ref_close)}</span>
      {/if}
    </div>
    {#if !panel.error}
      <div class="chips">
        <span class="regime regime-{panel.regime_1d}">
          1D {REGIME_LABEL[panel.regime_1d] ?? panel.regime_1d}
        </span>
        <span class="regime regime-{panel.regime_4h}">
          4H {REGIME_LABEL[panel.regime_4h] ?? panel.regime_4h}
        </span>
      </div>
    {/if}
  </header>

  {#if panel.error}
    <ErrorBanner error={panel.error} />
  {:else}
    <div class="meta">
      <span><span class="k">ATR14</span> {fmtPrice(panel.atr14)}</span>
      {#if panel.adr_pct !== null}
        <span><span class="k">ADR</span> {fmtPct(panel.adr_pct)}</span>
      {/if}
    </div>

    {#if scope.on.levels}
      <Block label="Levels">
        <PriceLadder
          levelsAbove={panel.levels_above}
          levelsBelow={panel.levels_below}
          refClose={panel.ref_close}
          refPriceSource={panel.ref_price_source}
        />
      </Block>
    {/if}

    {#if scope.on.zones}
      <Block label="Zones">
        <ZonesRow zonesAbove={panel.zones_above} zonesBelow={panel.zones_below} />
      </Block>
    {/if}

    {#if scope.on.indicators && panel.indicators}
      <Block label="Indicators">
        <IndicatorBlock ind={panel.indicators} />
      </Block>
    {/if}

    {#if scope.on.sessions && panel.sessions}
      <Block label="Sessions" note="MYT">
        <SessionBlock sessions={panel.sessions} />
      </Block>
    {/if}

    {#if showPeriods}
      <Block label="Month / Week">
        <PeriodBlock monthly={panel.monthly} weekly={panel.weekly} />
      </Block>
    {/if}

    {#if scope.on.external && panel.external}
      <Block label="External" note="{panel.external.snapshots.length} snapshot{panel.external.snapshots.length === 1 ? '' : 's'}">
        <ExternalBlock external={panel.external} />
      </Block>
    {/if}

    {#if scope.on.seasonality && panel.seasonality}
      <Block label="Seasonality">
        <SeasonalityStrip seasonality={panel.seasonality} />
      </Block>
    {/if}
  {/if}
</article>

<style>
  .panel {
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 14px 16px 16px;
  }

  .head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
    padding-bottom: 9px;
    border-bottom: 1px solid var(--border);
  }

  .ident {
    display: flex;
    align-items: baseline;
    gap: 10px;
  }

  /* The one large thing per card. A symbol card is identified by its ticker and
     its price, and everything else is subordinate to those two. */
  .ticker {
    font-size: var(--fs-lead);
    font-weight: 700;
    letter-spacing: 0.08em;
    color: var(--text);
  }

  .last {
    font-size: var(--fs-lead);
    font-weight: 700;
    color: var(--accent);
    font-variant-numeric: tabular-nums;
  }

  .chips { display: flex; gap: 5px; }

  .regime {
    font-size: 9px;
    letter-spacing: 0.05em;
    padding: 2px 6px;
    border-radius: 3px;
    border: 1px solid var(--border);
    color: var(--text-soft);
  }

  .regime.regime-trend {
    color: var(--accent);
    border-color: color-mix(in srgb, var(--accent) 35%, transparent);
  }
  .regime.regime-high_vol {
    color: var(--yellow);
    border-color: color-mix(in srgb, var(--yellow) 35%, transparent);
  }
  .regime.regime-range { color: var(--text-dim); }
  .regime.regime-unknown { color: var(--muted); }

  .meta {
    display: flex;
    gap: 14px;
    padding: 8px 0 2px;
    font-size: var(--fs-micro);
    color: var(--text-dim);
    font-variant-numeric: tabular-nums;
  }

  .k {
    color: var(--muted);
    letter-spacing: 0.05em;
    text-transform: uppercase;
    margin-right: 3px;
  }
</style>
