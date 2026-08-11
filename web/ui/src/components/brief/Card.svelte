<script lang="ts">
  import type { Snippet } from "svelte";

  // The card frame, shared by the symbol panels, the pundit board, the legend
  // and the health footer. Svelte scopes styles per component, so without this
  // the same frame CSS would be copy-pasted into four files and drift.
  //
  // Do not write a literal style/script tag in a comment here — the parser
  // reads it out of the script block and reports the script as left open.

  type Props = {
    title: string;
    /** Right side of the header — chips, counts, status. */
    aside?: Snippet;
    children: Snippet;
  };

  let { title, aside, children }: Props = $props();
</script>

<section class="card">
  <header class="card-header">
    <h3 class="card-title">{title}</h3>
    {#if aside}
      <div class="card-aside">{@render aside()}</div>
    {/if}
  </header>
  {@render children()}
</section>

<style>
  .card {
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 14px 16px 16px;
  }

  .card-header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
    padding-bottom: 9px;
    margin-bottom: 11px;
    border-bottom: 1px solid var(--border);
  }

  .card-title {
    font-size: var(--fs-lead);
    font-weight: 700;
    letter-spacing: 0.06em;
    color: var(--text);
  }

  .card-aside {
    display: flex;
    align-items: baseline;
    gap: 8px;
    flex-wrap: wrap;
  }
</style>
