<script lang="ts">
  import { SCOPES, scope } from "./scope.svelte";
</script>

<div class="scope">
  <span class="label" id="scope-label">Show</span>
  <div class="toggles" role="group" aria-labelledby="scope-label">
    {#each SCOPES as s}
      <button
        type="button"
        class="toggle"
        class:on={scope.on[s.key]}
        aria-pressed={scope.on[s.key]}
        onclick={() => scope.toggle(s.key)}
      >
        {s.label}
      </button>
    {/each}
  </div>
  {#if scope.hiddenCount > 0}
    <button type="button" class="reset" onclick={() => scope.showAll()}>
      show all ({scope.hiddenCount} hidden)
    </button>
  {/if}
</div>

<style>
  .scope {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    padding: 9px 12px;
    margin-top: 14px;
    background: var(--bg-panel);
    border: 1px solid var(--border-dim);
    border-radius: 6px;
  }

  .label {
    font-size: var(--fs-eyebrow);
    font-weight: 700;
    letter-spacing: var(--ls-eyebrow);
    text-transform: uppercase;
    color: var(--text-soft);
  }

  .toggles { display: flex; flex-wrap: wrap; gap: 5px; }

  /* Overrides the global button rule, which is sized for primary actions and
     would make nine of these dominate the page. */
  .toggle {
    font-size: var(--fs-micro);
    font-weight: 400;
    letter-spacing: 0.04em;
    text-transform: none;
    padding: 3px 9px;
    border-radius: 3px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--muted);
    transition: color 100ms, border-color 100ms, background 100ms;
  }

  .toggle:hover:not(:disabled) {
    background: transparent;
    color: var(--text-dim);
    border-color: var(--text-soft);
  }

  .toggle.on {
    color: var(--accent);
    border-color: color-mix(in srgb, var(--accent) 45%, transparent);
    background: color-mix(in srgb, var(--accent) 9%, transparent);
  }

  .toggle:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  .reset {
    margin-left: auto;
    font-size: var(--fs-micro);
    font-weight: 400;
    letter-spacing: 0.04em;
    text-transform: none;
    padding: 3px 9px;
    border-color: var(--border);
    color: var(--text-soft);
  }

  @media (prefers-reduced-motion: reduce) {
    .toggle { transition: none; }
  }
</style>
