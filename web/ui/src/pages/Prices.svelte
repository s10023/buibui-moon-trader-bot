<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import {
    pricesConnected,
    pricesStore,
    startPricesSSE,
    stopPricesSSE,
  } from "../stores/prices";
  import LoadingSpinner from "../components/LoadingSpinner.svelte";
  import PriceRow from "../components/PriceRow.svelte";

  onMount(startPricesSSE);
  onDestroy(stopPricesSSE);

  const sorted = $derived([...$pricesStore.values()]);
</script>

<div class="page">
  <div class="page-header">
    <h2>Prices</h2>
    <div class="conn-badge" class:live={$pricesConnected}>
      <span class="live-dot" class:offline={!$pricesConnected}></span>
      {$pricesConnected ? "Live" : "Connecting"}
    </div>
  </div>

  {#if !$pricesConnected && sorted.length === 0}
    <LoadingSpinner label="Connecting to price stream..." />
  {:else}
    <table>
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Price</th>
          <th>15m</th>
          <th>1h</th>
          <th>4h</th>
          <th>Asia</th>
          <th>24h</th>
        </tr>
      </thead>
      <tbody>
        {#each sorted as row (row.symbol)}
          <PriceRow {row} />
        {/each}
      </tbody>
    </table>
  {/if}
</div>

<style>
  .page-header {
    display: flex;
    align-items: baseline;
    gap: 12px;
  }

  .conn-badge {
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
    display: flex;
    align-items: center;
    padding-bottom: 14px;
  }

  .conn-badge.live { color: var(--green); }
</style>
