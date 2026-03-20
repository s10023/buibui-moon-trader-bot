<script lang="ts">
  import { onDestroy, onMount } from "svelte";

  const links = [
    { href: "#/chart",     label: "Chart" },
    { href: "#/backtest",  label: "Backtest" },
    { href: "#/signals",   label: "Signals" },
    { href: "#/positions", label: "Positions" },
    { href: "#/prices",    label: "Prices" },
  ];

  let { route }: { route: string } = $props();

  let clock = $state("");

  function tick() {
    clock = new Date().toLocaleTimeString("en-MY", {
      timeZone: "Asia/Kuala_Lumpur",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }

  let interval: ReturnType<typeof setInterval>;
  onMount(() => { tick(); interval = setInterval(tick, 1000); });
  onDestroy(() => clearInterval(interval));
</script>

<header>
  <div class="brand">
    <span class="bracket">[</span>
    <span class="brand-name">BUIBUI</span>
    <span class="bracket">]</span>
    <span class="brand-sub">// TERMINAL</span>
  </div>

  <nav>
    {#each links as { href, label }}
      <a {href} class:active={route === href}>
        <span class="link-text">{label}</span>
      </a>
    {/each}
  </nav>

  <div class="status-bar">
    <span class="live-dot"></span>
    <span class="clock">{clock}</span>
    <span class="tz">MYT</span>
  </div>
</header>

<style>
  header {
    display: flex;
    align-items: center;
    height: var(--nav-h);
    background: var(--bg-panel);
    border-bottom: 1px solid var(--border);
    padding: 0 20px;
    gap: 0;
    position: sticky;
    top: 0;
    z-index: 100;
    backdrop-filter: blur(8px);
  }

  /* Brand */
  .brand {
    display: flex;
    align-items: center;
    gap: 3px;
    margin-right: 28px;
    flex-shrink: 0;
  }

  .bracket {
    color: var(--muted);
    font-size: 14px;
    font-weight: 400;
  }

  .brand-name {
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.15em;
    color: var(--accent);
  }

  .brand-sub {
    font-size: 9px;
    letter-spacing: 0.12em;
    color: var(--muted);
    margin-left: 8px;
    padding-top: 2px;
    display: none;
  }

  @media (min-width: 700px) {
    .brand-sub { display: inline; }
  }

  /* Nav links */
  nav {
    display: flex;
    flex: 1;
    height: 100%;
  }

  a {
    display: flex;
    align-items: center;
    padding: 0 14px;
    height: 100%;
    color: var(--muted);
    text-decoration: none;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    border-bottom: 2px solid transparent;
    transition: color 120ms;
    position: relative;
  }

  a:hover { color: var(--text); }

  a.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }

  a.active .link-text::before {
    content: ">";
    position: absolute;
    left: 4px;
    color: var(--accent);
    font-size: 9px;
    opacity: 0.7;
  }

  /* Clock */
  .status-bar {
    display: flex;
    align-items: center;
    gap: 4px;
    margin-left: auto;
    flex-shrink: 0;
  }

  .clock {
    font-size: 12px;
    font-feature-settings: "tnum" 1;
    color: var(--text-dim);
    letter-spacing: 0.05em;
  }

  .tz {
    font-size: 9px;
    letter-spacing: 0.1em;
    color: var(--muted);
    padding-top: 1px;
  }
</style>
