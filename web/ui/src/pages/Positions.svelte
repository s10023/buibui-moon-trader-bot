<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import {
    positionsConnected,
    positionsStore,
    startPositionsSSE,
    stopPositionsSSE,
  } from "../stores/positions";
  import LoadingSpinner from "../components/LoadingSpinner.svelte";
  import PositionRow from "../components/PositionRow.svelte";

  onMount(startPositionsSSE);
  onDestroy(stopPositionsSSE);

  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  // ── Toast ─────────────────────────────────────────────────────────────────
  let toastMsg = $state("");
  let toastOk = $state(true);
  let toastTimer: ReturnType<typeof setTimeout> | null = null;

  function showToast(msg: string, ok: boolean) {
    toastMsg = msg;
    toastOk = ok;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastMsg = ""; }, 4000);
  }
</script>

<div class="page">
  <div class="page-header">
    <h2>Positions</h2>
    <div class="conn-badge" class:live={$positionsConnected}>
      <span class="live-dot" class:offline={!$positionsConnected}></span>
      {$positionsConnected ? "Live" : "Connecting"}
    </div>
  </div>

  {#if toastMsg}
    <div class="toast" class:toast-ok={toastOk} class:toast-err={!toastOk}>
      {toastMsg}
    </div>
  {/if}

  {#if !$positionsConnected && $positionsStore.positions.length === 0}
    <LoadingSpinner label="Connecting to positions stream..." />
  {:else}
    {@const d = $positionsStore}

    <div class="wallet-strip">
      <div class="wallet-item">
        <span class="wl">Wallet</span>
        <span class="wv">${fmt(d.wallet_balance)}</span>
      </div>
      <div class="wallet-item">
        <span class="wl">Available</span>
        <span class="wv">${fmt(d.available_balance)}</span>
      </div>
      <div class="wallet-item">
        <span class="wl">Unrealized PnL</span>
        <span class="wv" class:green={d.unrealized_pnl >= 0} class:red={d.unrealized_pnl < 0}>
          {d.unrealized_pnl >= 0 ? "+" : ""}{fmt(d.unrealized_pnl)}
        </span>
      </div>
      <div class="wallet-item">
        <span class="wl">Total Risk</span>
        <span class="wv red">${fmt(d.total_risk_usd)}</span>
      </div>
    </div>

    {#if d.positions.length === 0}
      <p class="muted empty">// no open positions</p>
    {:else}
      <table>
        <thead>
          <tr>
            <th>Symbol</th><th>Side</th><th>Lev</th><th>Entry</th><th>Mark</th>
            <th>PnL ($)</th><th>PnL %</th><th>SL</th><th>Risk</th><th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {#each d.positions as pos (pos.symbol)}
            <PositionRow {pos} onAction={showToast} />
          {/each}
        </tbody>
      </table>
    {/if}
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

  .wallet-strip {
    display: flex;
    gap: 0;
    margin-bottom: 16px;
    border: 1px solid var(--border-dim);
    border-radius: 4px;
    overflow: hidden;
  }

  .wallet-item {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 10px 16px;
    background: var(--bg-panel);
    border-right: 1px solid var(--border-dim);
  }

  .wallet-item:last-child { border-right: none; }

  .wl {
    font-size: 9px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
  }

  .wv {
    font-size: 15px;
    font-weight: 500;
    font-feature-settings: "tnum" 1;
    color: var(--text);
  }

  .empty {
    padding: 20px 0;
    font-size: 12px;
    letter-spacing: 0.05em;
  }

  /* ── Toast ──────────────────────────────────────────────────────────────── */
  .toast {
    position: fixed;
    bottom: 24px;
    right: 24px;
    padding: 10px 18px;
    border-radius: 4px;
    font-size: 12px;
    letter-spacing: 0.03em;
    z-index: 1000;
    pointer-events: none;
    max-width: 360px;
  }

  .toast-ok {
    background: #1a2a1a;
    border: 1px solid var(--green);
    color: var(--green);
  }

  .toast-err {
    background: #2a1a1a;
    border: 1px solid var(--red);
    color: var(--red);
  }
</style>
