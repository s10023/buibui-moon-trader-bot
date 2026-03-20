<script lang="ts">
  import type { PositionRow } from "../api";
  let { pos }: { pos: PositionRow } = $props();

  const sideClass = $derived(
    pos.side.toLowerCase().includes("long") ? "green" : "red"
  );
  const pnlClass = $derived(
    pos.pnl == null ? "" : pos.pnl >= 0 ? "green" : "red"
  );
  const fmt = (v: number | null, digits = 2) =>
    v == null ? "–" : v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const fmtPct = (v: number | null) => (v == null ? "–" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`);
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
</tr>
