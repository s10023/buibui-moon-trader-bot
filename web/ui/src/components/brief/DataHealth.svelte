<script lang="ts">
  import type { BriefHealthReport } from "../../api";
  import Card from "./Card.svelte";

  type Props = { health: BriefHealthReport };
  let { health }: Props = $props();

  let bad = $derived(health.rows.filter((r) => r.status !== "ok").length);
</script>

<Card title="Data Health">
  {#snippet aside()}
    <span class="summary" class:clean={bad === 0}>
      {bad === 0 ? `all ${health.rows.length} series current` : `${bad} of ${health.rows.length} behind`}
    </span>
  {/snippet}

  <div class="chips">
    {#each health.rows as row}
      <span class="chip status-{row.status}">
        {row.symbol}/{row.tf}
        <span class="state">
          {row.status === "ok" ? "✓" : `${row.status} ${row.bars_behind}b`}
        </span>
      </span>
    {/each}
  </div>

  {#each health.notes as note}
    <p class="note">{note}</p>
  {/each}
</Card>

<style>
  /* The header now states the conclusion, so a clean run can be read without
     parsing 25 individual chips. */
  .summary { font-size: var(--fs-annot); color: var(--yellow); }
  .summary.clean { color: var(--text-soft); }

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
  }

  .chip {
    display: inline-flex;
    align-items: baseline;
    gap: 4px;
    font-size: var(--fs-micro);
    letter-spacing: 0.03em;
    padding: 2px 7px;
    border-radius: 3px;
    border: 1px solid var(--border);
    color: var(--text-soft);
  }

  .state { color: var(--muted); }

  .chip.status-ok { color: var(--text-soft); }
  .chip.status-ok .state { color: var(--accent); }

  .chip.status-stale {
    color: var(--yellow);
    border-color: color-mix(in srgb, var(--yellow) 35%, transparent);
  }
  .chip.status-stale .state { color: var(--yellow); }

  .chip.status-missing {
    color: var(--red);
    border-color: color-mix(in srgb, var(--red) 35%, transparent);
  }
  .chip.status-missing .state { color: var(--red); }

  .note {
    margin-top: 7px;
    font-size: var(--fs-annot);
    color: var(--text-soft);
    line-height: 1.6;
  }

  .note::before {
    content: "note ";
    color: var(--muted);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    font-size: var(--fs-eyebrow);
  }
</style>
