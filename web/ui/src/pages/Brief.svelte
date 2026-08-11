<script lang="ts">
  import { onMount } from "svelte";
  import { getBrief, type BriefResponse } from "../api";
  import LoadingSpinner from "../components/LoadingSpinner.svelte";
  import ErrorBanner from "../components/ErrorBanner.svelte";
  import BriefLegend from "../components/brief/BriefLegend.svelte";
  import DataHealth from "../components/brief/DataHealth.svelte";
  import PunditBoard from "../components/brief/PunditBoard.svelte";
  import ScopeBar from "../components/brief/ScopeBar.svelte";
  import SymbolPanel from "../components/brief/SymbolPanel.svelte";
  import { asOfMyt, mytHHMM } from "../components/brief/format";
  import { scope } from "../components/brief/scope.svelte";

  // This page is a shell. Every card lives in components/brief/ — it was one
  // 1,102-line file with a single heading in it, which is why it read as an
  // undifferentiated block of text rather than something scannable.

  let data = $state<BriefResponse | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let showLegend = $state(false);

  async function load(): Promise<void> {
    loading = true;
    error = null;
    try {
      data = await getBrief();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    void load();
  });
</script>

<div class="page">
  <div class="page-header">
    <h2>Daily Brief</h2>
    <div class="controls">
      {#if data}
        <span class="day-ahead">{data.day_ahead}</span>
        <span class="as-of">as-of {asOfMyt(data.as_of_ms)} MYT</span>
        <span class="pill health" class:ok={data.health.data_ok} class:warn={!data.health.data_ok}>
          {data.health.data_ok ? "● data ok" : "⚠ data issue"}
        </span>
        {#if data.session_clock}
          {@const clk = data.session_clock}
          <span class="pill clock">
            {clk.label === "Off" ? "between sessions" : clk.label}{clk.is_overlap
              ? " (NY overlap)"
              : ""}
            · next {clk.next_label} {mytHHMM(clk.next_start_ms)} MYT
          </span>
        {/if}
      {/if}
      <button class="btn-sm" aria-pressed={showLegend} onclick={() => (showLegend = !showLegend)}>
        ⓘ legend
      </button>
      <button class="btn-sm" onclick={() => void load()} disabled={loading}>
        {loading ? "Loading…" : "Refresh"}
      </button>
    </div>
  </div>

  {#if showLegend}
    <div class="legend-slot"><BriefLegend /></div>
  {/if}

  {#if error}
    <ErrorBanner {error} />
  {/if}

  {#if loading && !data}
    <LoadingSpinner label="Computing brief…" />
  {:else if data}
    <ScopeBar />

    <div class="panels">
      {#each data.panels as panel (panel.symbol)}
        <SymbolPanel {panel} />
      {/each}
    </div>

    {#if scope.on.pundit}
      <div class="wide"><PunditBoard pundit={data.pundit} /></div>
    {/if}

    {#if scope.on.health}
      <div class="wide"><DataHealth health={data.health} /></div>
    {/if}
  {/if}
</div>

<style>
  .page-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
  }

  .controls {
    display: flex;
    align-items: baseline;
    gap: 10px;
    flex-wrap: wrap;
    font-size: var(--fs-annot);
  }

  .day-ahead {
    color: var(--text);
    font-weight: 700;
    letter-spacing: 0.04em;
  }

  .as-of { color: var(--text-soft); }

  .pill {
    font-size: var(--fs-micro);
    letter-spacing: 0.04em;
    padding: 2px 8px;
    border-radius: 3px;
    border: 1px solid var(--border);
    color: var(--text-soft);
  }

  .pill.health { text-transform: uppercase; letter-spacing: 0.06em; }
  .pill.health.ok {
    color: var(--accent);
    border-color: color-mix(in srgb, var(--accent) 40%, transparent);
  }
  .pill.health.warn {
    color: var(--yellow);
    border-color: color-mix(in srgb, var(--yellow) 40%, transparent);
  }

  .btn-sm { font-size: var(--fs-micro); padding: 5px 12px; }

  .legend-slot { margin-bottom: 16px; }

  .panels {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
    gap: 16px;
    margin-top: 16px;
  }

  .wide { margin-top: 16px; }
</style>
