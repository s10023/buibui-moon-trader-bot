<script lang="ts">
  import type { PriceRow } from "../api";
  let { row }: { row: PriceRow } = $props();

  function pctClass(v: string): string {
    const n = parseFloat(v);
    if (isNaN(n) || n === 0) return "";
    return n > 0 ? "green" : "red";
  }
</script>

<tr>
  <td class="mono">{row.symbol}</td>
  <td class="mono">{row.last_price}</td>
  <td class={pctClass(row.change_15m)}>{row.change_15m}</td>
  <td class={pctClass(row.change_1h)}>{row.change_1h}</td>
  <td class={pctClass(row.change_4h)}>{row.change_4h}</td>
  <td class={pctClass(row.change_asia)}>{row.change_asia}</td>
  <td class={pctClass(row.change_24h)}>{row.change_24h}</td>
</tr>
