<script lang="ts">
  import type { BriefExternalState } from "../../api";
  import { extCluster, panelShort } from "./format";

  type Props = { external: BriefExternalState };
  let { external }: Props = $props();
</script>

<div class="snaps">
  {#each external.snapshots as snap}
    <div class="snap">
      <div class="snap-head">
        <span class="src">
          {snap.source}{snap.venue ? `/${snap.venue}` : ""}
        </span>
        <span class="panel-tag">{panelShort(snap.panel)}</span>
        {#if snap.window}<span class="meta">{snap.window}</span>{/if}
        {#if snap.scope === "agg"}<span class="meta">agg</span>{/if}
        <span class="age">{Math.round(snap.age_hours)}h</span>
        {#if snap.spot_hint_deviation}<span class="warn">⚠ spot</span>{/if}
      </div>
      <div class="side">
        <span class="side-tag above">above</span>
        {#if snap.clusters_above.length}
          <span class="clusters">
            {#each snap.clusters_above as c, i}<span class="cluster"
                >{extCluster(c)}</span
              >{#if i < snap.clusters_above.length - 1}<span class="sep">·</span>{/if}{/each}
          </span>
        {:else}<span class="none">none</span>{/if}
      </div>
      <div class="side">
        <span class="side-tag below">below</span>
        {#if snap.clusters_below.length}
          <span class="clusters">
            {#each snap.clusters_below as c, i}<span class="cluster"
                >{extCluster(c)}</span
              >{#if i < snap.clusters_below.length - 1}<span class="sep">·</span>{/if}{/each}
          </span>
        {:else}<span class="none">none</span>{/if}
      </div>
    </div>
  {/each}
</div>

<style>
  .snaps {
    display: grid;
    gap: 8px;
    font-variant-numeric: tabular-nums;
  }

  .snap-head {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 3px;
  }

  .src { color: var(--text); font-size: var(--fs-annot); }

  .panel-tag {
    font-size: 8px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--accent);
    background: var(--accent-dim);
    border-radius: 2px;
    padding: 1px 5px;
  }

  .meta { color: var(--muted); font-size: var(--fs-micro); }
  .age { color: var(--text-soft); font-size: var(--fs-micro); margin-left: auto; }
  .warn { color: var(--yellow); font-size: var(--fs-micro); }

  /* Above and below are the axis of this block, so they get the same colour
     language as the ladder — a reader moving between the two blocks should not
     have to relearn which side is which. */
  .side {
    display: grid;
    grid-template-columns: 42px 1fr;
    gap: 8px;
    align-items: baseline;
  }

  .side-tag {
    font-size: 8px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .side-tag.above { color: color-mix(in srgb, var(--red) 78%, var(--text-soft)); }
  .side-tag.below { color: color-mix(in srgb, var(--green) 78%, var(--text-soft)); }

  .clusters { color: var(--text-dim); font-size: var(--fs-micro); }
  .cluster { white-space: nowrap; }
  .sep { color: var(--muted); margin: 0 4px; }
  .none { color: var(--muted); font-size: var(--fs-micro); }
</style>
