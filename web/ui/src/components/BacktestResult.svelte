<script lang="ts">
  import type { BacktestResponse } from "../api";
  let { result }: { result: BacktestResponse } = $props();

  const equitySeries = $derived(() => {
    let cum = 0;
    const pts: number[] = [0];
    for (const t of result.trades) {
      if (t.pnl_r != null) {
        cum += t.pnl_r;
        pts.push(cum);
      }
    }
    return pts;
  });

  const svgPath = $derived(() => {
    const pts = equitySeries();
    if (pts.length < 2) return "";
    const minVal = Math.min(...pts);
    const maxVal = Math.max(...pts);
    const range = maxVal - minVal || 1;
    const W = 800, H = 100, PAD = 6;
    const xs = pts.map((_, i) => PAD + (i / (pts.length - 1)) * (W - PAD * 2));
    const ys = pts.map(
      (v) => H - PAD - ((v - minVal) / range) * (H - PAD * 2)
    );
    return xs
      .map((x, i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${ys[i].toFixed(1)}`)
      .join(" ");
  });

  const fillPath = $derived(() => {
    const line = svgPath();
    if (!line) return "";
    return `${line} L800,100 L0,100 Z`;
  });

  const finalR = $derived(equitySeries()[equitySeries().length - 1] ?? 0);
  const curveColor = $derived(finalR >= 0 ? "var(--green)" : "var(--red)");
  const wr = $derived(`${(result.win_rate * 100).toFixed(1)}%`);
  const totalRClass = $derived(result.total_r >= 0 ? "green" : "red");
</script>

<div class="result">
  <div class="stat-strip">
    <div class="stat">
      <span class="sl">Trades</span>
      <span class="sv">{result.total_trades}</span>
    </div>
    <div class="stat">
      <span class="sl">Win / Loss</span>
      <span class="sv"><span class="green">{result.win_count}</span> / <span class="red">{result.loss_count}</span></span>
    </div>
    <div class="stat">
      <span class="sl">Win Rate</span>
      <span class="sv">{wr}</span>
    </div>
    <div class="stat">
      <span class="sl">Avg R</span>
      <span class="sv">{result.avg_r.toFixed(2)}</span>
    </div>
    <div class="stat">
      <span class="sl">Total R</span>
      <span class="sv {totalRClass}">{result.total_r >= 0 ? "+" : ""}{result.total_r.toFixed(2)}</span>
    </div>
    <div class="stat">
      <span class="sl">Max Drawdown</span>
      <span class="sv red">{result.max_drawdown_r.toFixed(2)}</span>
    </div>
  </div>

  {#if equitySeries().length > 1}
    <div class="chart-wrap">
      <svg viewBox="0 0 800 100" preserveAspectRatio="none" width="100%" height="100">
        <defs>
          <linearGradient id="eq-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color={curveColor} stop-opacity="0.18" />
            <stop offset="100%" stop-color={curveColor} stop-opacity="0" />
          </linearGradient>
        </defs>
        <path d={fillPath()} fill="url(#eq-fill)" />
        <path d={svgPath()} fill="none" stroke={curveColor} stroke-width="1.5" stroke-linejoin="round" />
      </svg>
    </div>
  {/if}

  <table>
    <thead>
      <tr><th>#</th><th>Dir</th><th>Entry</th><th>Exit</th><th>Outcome</th><th>PnL R</th></tr>
    </thead>
    <tbody>
      {#each [...result.trades].reverse() as t, i}
        {@const cls = t.outcome === "win" ? "green" : t.outcome === "loss" ? "red" : "muted"}
        <tr>
          <td class="muted">{result.trades.length - i}</td>
          <td class={t.direction === "long" ? "green" : "red"}>{t.direction.toUpperCase()}</td>
          <td class="mono">{t.entry_price.toLocaleString()}</td>
          <td class="mono">{t.exit_price != null ? t.exit_price.toLocaleString() : "—"}</td>
          <td class={cls}>{t.outcome}</td>
          <td class="{cls} mono">{t.pnl_r != null ? (t.pnl_r >= 0 ? "+" : "") + t.pnl_r.toFixed(2) : "—"}</td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  .result { margin-top: 20px; }

  .stat-strip {
    display: flex;
    gap: 0;
    margin-bottom: 14px;
    border: 1px solid var(--border-dim);
    border-radius: 4px;
    overflow: hidden;
  }

  .stat {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 10px 14px;
    background: var(--bg-panel);
    border-right: 1px solid var(--border-dim);
  }

  .stat:last-child { border-right: none; }

  .sl {
    font-size: 9px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
  }

  .sv {
    font-size: 15px;
    font-weight: 500;
    font-feature-settings: "tnum" 1;
    color: var(--text);
  }

  .chart-wrap {
    background: var(--bg-panel);
    border: 1px solid var(--border-dim);
    border-radius: 4px;
    margin-bottom: 14px;
    overflow: hidden;
  }

  .chart-wrap svg { display: block; }
</style>
