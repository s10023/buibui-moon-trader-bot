<script lang="ts">
  import type { BriefZoneRow } from "../../api";
  import { fmtDist, fmtPrice } from "./format";

  type Props = { zonesAbove: BriefZoneRow[]; zonesBelow: BriefZoneRow[] };
  let { zonesAbove, zonesBelow }: Props = $props();

  let zones = $derived([...zonesAbove, ...zonesBelow]);
</script>

<div class="zones">
  {#each zones as z}
    <span class="chip zone-{z.direction}" class:inside={z.inside}>
      <span class="tf">{z.tf}</span>
      <span class="kind">{z.zone_type.toUpperCase()}</span>
      <span class="band num">
        {z.zone_low === z.zone_high
          ? fmtPrice(z.zone_low)
          : `${fmtPrice(z.zone_low)}–${fmtPrice(z.zone_high)}`}
      </span>
      <span class="dist num">{z.inside ? "inside" : fmtDist(z.dist_atr)}</span>
    </span>
  {:else}
    <span class="empty">no active zones</span>
  {/each}
</div>

<style>
  .zones {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
    font-size: var(--fs-micro);
  }

  .chip {
    display: inline-flex;
    align-items: baseline;
    gap: 5px;
    padding: 2px 7px;
    border-radius: 3px;
    background: var(--bg);
    border-left: 2px solid var(--border);
    color: var(--text-soft);
    font-variant-numeric: tabular-nums;
  }

  .chip.zone-bull { border-left-color: var(--green); }
  .chip.zone-bear { border-left-color: var(--red); }

  /* "inside" means price is in the zone right now — the only zone state that
     changes what you do this minute, so it is the only one that gets weight. */
  .chip.inside {
    background: color-mix(in srgb, var(--accent) 9%, var(--bg));
    color: var(--text-dim);
  }

  .tf { color: var(--muted); }
  .kind { color: var(--text-dim); letter-spacing: 0.04em; }
  .band { color: var(--text-soft); }
  .dist { color: var(--muted); }
  .chip.inside .dist { color: var(--accent); }

  .empty { color: var(--muted); }
</style>
