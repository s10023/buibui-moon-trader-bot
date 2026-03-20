<script lang="ts">
  import { onMount } from "svelte";
  import { loadConfig } from "./stores/config";
  import { loadStrategies } from "./stores/strategies";
  import Nav from "./components/Nav.svelte";
  import Chart from "./pages/Chart.svelte";
  import Backtest from "./pages/Backtest.svelte";
  import SignalFeed from "./pages/SignalFeed.svelte";
  import Positions from "./pages/Positions.svelte";
  import Prices from "./pages/Prices.svelte";

  let route = $state(window.location.hash || "#/chart");

  onMount(() => {
    void loadConfig();
    void loadStrategies();
    const onHash = () => {
      route = window.location.hash;
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  });
</script>

<Nav {route} />

{#if route === "#/chart"}
  <Chart />
{:else if route === "#/backtest"}
  <Backtest />
{:else if route === "#/signals"}
  <SignalFeed />
{:else if route === "#/positions"}
  <Positions />
{:else if route === "#/prices"}
  <Prices />
{:else}
  <Chart />
{/if}
