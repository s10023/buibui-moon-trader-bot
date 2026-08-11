<script lang="ts">
  import type { Snippet } from "svelte";

  // One labelled block inside a symbol card.
  //
  // The Brief's readability problem was that Sessions, Month, Week and External
  // all rendered through one shared class, so four different kinds of
  // information looked identical. Every block now carries its own eyebrow and
  // its own hairline, which is the entire difference between a wall of text and
  // something scannable. `note` is the optional right-aligned counterweight
  // (a count, an age) that belongs with the label rather than in the body.

  type Props = {
    label: string;
    note?: string;
    children: Snippet;
  };

  let { label, note = "", children }: Props = $props();
</script>

<section class="block">
  <h4 class="eyebrow">
    <span>{label}</span>
    {#if note}<span class="note">{note}</span>{/if}
  </h4>
  <div class="body">
    {@render children()}
  </div>
</section>

<style>
  .block {
    border-top: 1px solid var(--border-dim);
    padding-top: 8px;
    margin-top: 8px;
  }

  /* The first block in a card sits directly under the card header, which
     already draws its own separation. */
  .block:first-of-type {
    border-top: none;
    padding-top: 0;
    margin-top: 0;
  }

  .eyebrow {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
    font-size: var(--fs-eyebrow);
    font-weight: 700;
    letter-spacing: var(--ls-eyebrow);
    text-transform: uppercase;
    color: var(--text-soft);
    margin-bottom: 5px;
  }

  .note {
    font-weight: 400;
    letter-spacing: 0.04em;
    text-transform: none;
    color: var(--muted);
  }

  .body {
    font-size: var(--fs-annot);
    color: var(--text-dim);
    font-variant-numeric: tabular-nums;
  }
</style>
