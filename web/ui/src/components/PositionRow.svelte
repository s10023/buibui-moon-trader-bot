<script lang="ts">
  import type { PositionRow } from "../api";
  import { closePosition, modifySl, modifyTp } from "../api";

  let { pos, onAction }: { pos: PositionRow; onAction: (msg: string, ok: boolean) => void } = $props();

  const sideClass = $derived(
    pos.side.toLowerCase().includes("long") ? "green" : "red"
  );
  const pnlClass = $derived(
    pos.pnl == null ? "" : pos.pnl >= 0 ? "green" : "red"
  );
  const fmt = (v: number | null, digits = 2) =>
    v == null ? "–" : v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const fmtPct = (v: number | null) => (v == null ? "–" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`);

  let busy = $state(false);
  let slInput = $state("");
  let tpInput = $state("");
  let showSlInput = $state(false);
  let showTpInput = $state(false);

  async function handleClose() {
    const confirmed = window.confirm(
      `Close ${pos.side} ${pos.symbol} at market?\n\nThis will place a reduce-only market order.`
    );
    if (!confirmed) return;
    busy = true;
    try {
      const res = await closePosition({ symbol: pos.symbol, position_side: pos.side });
      onAction(res.detail, res.ok);
    } catch (e) {
      onAction(e instanceof Error ? e.message : String(e), false);
    } finally {
      busy = false;
    }
  }

  async function handleSl() {
    const price = parseFloat(slInput);
    if (!slInput || isNaN(price) || price <= 0) return;
    const confirmed = window.confirm(
      `Set SL to ${price} for ${pos.side} ${pos.symbol}?`
    );
    if (!confirmed) return;
    busy = true;
    try {
      const res = await modifySl({ symbol: pos.symbol, position_side: pos.side, stop_price: price });
      onAction(res.detail, res.ok);
      showSlInput = false;
      slInput = "";
    } catch (e) {
      onAction(e instanceof Error ? e.message : String(e), false);
    } finally {
      busy = false;
    }
  }

  async function handleTp() {
    const price = parseFloat(tpInput);
    if (!tpInput || isNaN(price) || price <= 0) return;
    const confirmed = window.confirm(
      `Set TP to ${price} for ${pos.side} ${pos.symbol}?`
    );
    if (!confirmed) return;
    busy = true;
    try {
      const res = await modifyTp({ symbol: pos.symbol, position_side: pos.side, stop_price: price });
      onAction(res.detail, res.ok);
      showTpInput = false;
      tpInput = "";
    } catch (e) {
      onAction(e instanceof Error ? e.message : String(e), false);
    } finally {
      busy = false;
    }
  }
</script>

<tr>
  <td class="mono">{pos.symbol}</td>
  <td class={sideClass}>{pos.side}</td>
  <td>{pos.leverage != null ? `${pos.leverage}x` : "–"}</td>
  <td class="mono">{fmt(pos.entry_price)}</td>
  <td class="mono">{fmt(pos.mark_price)}</td>
  <td class={pnlClass}>{fmt(pos.pnl)}</td>
  <td class={pnlClass}>{fmtPct(pos.pnl_pct)}</td>
  <td class="mono">{pos.sl_price != null ? fmt(pos.sl_price) : "–"}</td>
  <td class="muted">{pos.risk_pct ?? "–"}</td>
  <td class="actions">
    {#if showSlInput}
      <input
        class="price-input"
        type="number"
        step="any"
        placeholder="SL price"
        bind:value={slInput}
        onkeydown={(e) => { if (e.key === "Enter") handleSl(); if (e.key === "Escape") { showSlInput = false; slInput = ""; } }}
        disabled={busy}
      />
      <button class="btn-action" onclick={handleSl} disabled={busy}>OK</button>
      <button class="btn-cancel-input" onclick={() => { showSlInput = false; slInput = ""; }} disabled={busy}>✕</button>
    {:else if showTpInput}
      <input
        class="price-input"
        type="number"
        step="any"
        placeholder="TP price"
        bind:value={tpInput}
        onkeydown={(e) => { if (e.key === "Enter") handleTp(); if (e.key === "Escape") { showTpInput = false; tpInput = ""; } }}
        disabled={busy}
      />
      <button class="btn-action" onclick={handleTp} disabled={busy}>OK</button>
      <button class="btn-cancel-input" onclick={() => { showTpInput = false; tpInput = ""; }} disabled={busy}>✕</button>
    {:else}
      <button class="btn-sl" onclick={() => { showSlInput = true; }} disabled={busy} title="Modify SL">SL</button>
      <button class="btn-tp" onclick={() => { showTpInput = true; }} disabled={busy} title="Modify TP">TP</button>
      <button class="btn-close" onclick={handleClose} disabled={busy} title="Close position">✕</button>
    {/if}
  </td>
</tr>

<style>
  .actions {
    display: flex;
    align-items: center;
    gap: 4px;
    white-space: nowrap;
  }

  button {
    font-size: 10px;
    font-family: inherit;
    letter-spacing: 0.05em;
    border: 1px solid var(--border-dim);
    border-radius: 3px;
    background: transparent;
    cursor: pointer;
    padding: 2px 6px;
    transition: background 0.1s, color 0.1s;
  }

  button:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .btn-sl {
    color: var(--muted);
  }

  .btn-sl:not(:disabled):hover {
    background: var(--border-dim);
    color: var(--text);
  }

  .btn-tp {
    color: var(--muted);
  }

  .btn-tp:not(:disabled):hover {
    background: var(--border-dim);
    color: var(--text);
  }

  .btn-close {
    color: var(--red);
    border-color: var(--red);
  }

  .btn-close:not(:disabled):hover {
    background: var(--red);
    color: #000;
  }

  .btn-action {
    color: var(--green);
    border-color: var(--green);
  }

  .btn-action:not(:disabled):hover {
    background: var(--green);
    color: #000;
  }

  .btn-cancel-input {
    color: var(--muted);
  }

  .btn-cancel-input:not(:disabled):hover {
    background: var(--border-dim);
    color: var(--text);
  }

  .price-input {
    width: 90px;
    font-size: 11px;
    font-family: var(--font-mono, monospace);
    background: var(--bg);
    border: 1px solid var(--border-dim);
    border-radius: 3px;
    color: var(--text);
    padding: 2px 5px;
  }

  .price-input:focus {
    outline: none;
    border-color: var(--green);
  }
</style>
