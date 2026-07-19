<script lang="ts">
  import type {
    ConeComboResponse,
    PathConeResponse,
    TodayPathResponse,
  } from "../api";

  let {
    pathCone,
    todayPath,
  }: {
    pathCone: PathConeResponse;
    todayPath: TodayPathResponse | null;
  } = $props();

  const DIRS = [
    { key: "all", label: "All" },
    { key: "bull", label: "Bull" },
    { key: "bear", label: "Bear" },
  ];
  const DAYS = [
    { key: "all", label: "All days" },
    { key: "mon", label: "Mon" },
    { key: "tue", label: "Tue" },
    { key: "wed", label: "Wed" },
    { key: "thu", label: "Thu" },
    { key: "fri", label: "Fri" },
    { key: "sat", label: "Sat" },
    { key: "sun", label: "Sun" },
  ];

  let dir = $state("all");
  let wday = $state("all");

  const combo = $derived<ConeComboResponse | null>(
    pathCone.combos[`${dir}|${wday}`] ?? null
  );
  const hasData = $derived(!!combo && combo.n > 0);

  // ── SVG geometry (viewBox units) ──
  const W = 760;
  const H = 280;
  const PL = 46;
  const PR = 10;
  const PT = 12;
  const PB = 26;

  const yDomain = $derived.by(() => {
    const vals: number[] = [0];
    if (combo && combo.n > 0)
      for (const row of combo.bands) vals.push(row[0], row[4]);
    if (todayPath) vals.push(...todayPath.points);
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const pad = Math.max((hi - lo) * 0.08, 0.1);
    return { min: lo - pad, max: hi + pad };
  });

  const x = (step: number) => PL + (step / 24) * (W - PL - PR);
  const y = (v: number) =>
    PT + ((yDomain.max - v) / (yDomain.max - yDomain.min)) * (H - PT - PB);

  // Filled band polygon between percentile columns loIdx/hiIdx (0=p10 … 4=p90).
  function bandD(loIdx: number, hiIdx: number): string {
    if (!combo || combo.n === 0) return "";
    const pts = [`M ${x(0)} ${y(0)}`];
    combo.bands.forEach((row, i) => pts.push(`L ${x(i + 1)} ${y(row[hiIdx])}`));
    for (let i = combo.bands.length - 1; i >= 0; i--)
      pts.push(`L ${x(i + 1)} ${y(combo.bands[i][loIdx])}`);
    pts.push("Z");
    return pts.join(" ");
  }

  function lineD(idx: number): string {
    if (!combo || combo.n === 0) return "";
    return [
      `M ${x(0)} ${y(0)}`,
      ...combo.bands.map((row, i) => `L ${x(i + 1)} ${y(row[idx])}`),
    ].join(" ");
  }

  const todayD = $derived.by(() => {
    if (!todayPath || todayPath.points.length === 0) return "";
    return [
      `M ${x(0)} ${y(0)}`,
      ...todayPath.points.map((v, i) => `L ${x(i + 1)} ${y(v)}`),
    ].join(" ");
  });

  // Axis: elapsed UTC hour → MYT label (UTC+8, no DST)
  const ticks = [0, 4, 8, 12, 16, 20, 24].map((s) => ({
    step: s,
    label: String((s + 8) % 24).padStart(2, "0") + ":00",
  }));

  const fmtAdr = (v: number) => (v >= 0 ? "+" : "") + v.toFixed(2);
  const px = (mag: number, side: 1 | -1) =>
    todayPath
      ? todayPath.today_open * (1 + side * mag * todayPath.adr14_today)
      : null;
  const fmtPx = (p: number | null) =>
    p === null ? "—" : p >= 1000 ? p.toFixed(0) : p.toFixed(4);

  const elapsed = $derived(todayPath?.elapsed_h ?? 0);
  const lowInNow = $derived(
    hasData && elapsed >= 1 ? combo!.low_in_by[Math.min(elapsed, 24) - 1] : null
  );
  const highInNow = $derived(
    hasData && elapsed >= 1 ? combo!.high_in_by[Math.min(elapsed, 24) - 1] : null
  );
</script>

<div class="cone">
  <div class="cone-controls">
    <div class="chip-group">
      {#each DIRS as d}
        <button
          class="chip"
          class:active={dir === d.key}
          onclick={() => (dir = d.key)}>{d.label}</button
        >
      {/each}
    </div>
    <div class="chip-group">
      {#each DAYS as d}
        <button
          class="chip"
          class:active={wday === d.key}
          onclick={() => (wday = d.key)}>{d.label}</button
        >
      {/each}
    </div>
    {#if combo}
      <span class="n-badge" class:thin={combo.n < 30}
        >n={combo.n}{combo.n < 30 ? " ⚠ thin sample" : ""}</span
      >
    {/if}
  </div>

  {#if hasData && combo}
    <svg viewBox="0 0 {W} {H}" class="cone-svg" role="img" aria-label="Daily path cone">
      <path d={bandD(0, 4)} class="band-outer" />
      <path d={bandD(1, 3)} class="band-inner" />
      <line x1={x(0)} y1={y(0)} x2={x(24)} y2={y(0)} class="zero-line" />
      <path d={lineD(2)} class="median-line" />
      {#if todayD && todayPath}
        <path d={todayD} class="today-line" />
        <circle
          cx={x(todayPath.points.length)}
          cy={y(todayPath.points[todayPath.points.length - 1])}
          r="3.5"
          class="today-dot"
        />
      {/if}
      {#each ticks as t}
        <text x={x(t.step)} y={H - 8} class="axis-label" text-anchor="middle"
          >{t.label}</text
        >
      {/each}
      <text x={PL - 6} y={PT + 8} class="axis-label" text-anchor="end"
        >{fmtAdr(yDomain.max)}×</text
      >
      <text x={PL - 6} y={y(0) + 3} class="axis-label" text-anchor="end">0</text>
      <text x={PL - 6} y={H - PB} class="axis-label" text-anchor="end"
        >{fmtAdr(yDomain.min)}×</text
      >
    </svg>

    <div class="cone-footer">
      {#if lowInNow !== null && highInNow !== null}
        <span class="cone-stat"
          >by now: low in {(lowInNow * 100).toFixed(0)}% · high in {(
            highInNow * 100
          ).toFixed(0)}% of matching days</span
        >
      {/if}
      {#if todayPath}
        <span class="cone-stat"
          >pivots: H p50 {fmtPx(px(combo.high_piv[0], 1))} · H p80 {fmtPx(
            px(combo.high_piv[1], 1)
          )} · L p50 {fmtPx(px(combo.low_piv[0], -1))} · L p80 {fmtPx(
            px(combo.low_piv[1], -1)
          )}</span
        >
      {/if}
      <span class="cone-muted"
        >median day: dip {fmtAdr(combo.mae_p[1])}× / peak {fmtAdr(
          combo.mfe_p[1]
        )}× ADR</span
      >
      {#if todayPath}
        <span class="cone-muted"
          >1× ADR ≈ {(todayPath.adr14_today * 100).toFixed(2)}%</span
        >
      {/if}
    </div>
  {:else}
    <div class="cone-empty cone-muted">insufficient data for this filter</div>
  {/if}
</div>

<style>
  .cone {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .cone-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem 1rem;
    align-items: center;
  }
  .chip-group {
    display: flex;
    gap: 0.25rem;
    flex-wrap: wrap;
  }
  .chip {
    background: transparent;
    border: 1px solid #333;
    color: #aaa;
    border-radius: 4px;
    padding: 0.15rem 0.55rem;
    font-size: 0.78rem;
    cursor: pointer;
  }
  .chip.active {
    border-color: #60a5fa;
    color: #e5e7eb;
  }
  .n-badge {
    font-size: 0.75rem;
    color: #888;
  }
  .n-badge.thin {
    color: #f59e0b;
  }
  .cone-svg {
    width: 100%;
    height: auto;
  }
  .band-outer {
    fill: rgba(96, 165, 250, 0.1);
  }
  .band-inner {
    fill: rgba(96, 165, 250, 0.18);
  }
  .zero-line {
    stroke: #444;
    stroke-dasharray: 2 3;
  }
  .median-line {
    fill: none;
    stroke: #60a5fa;
    stroke-width: 1.5;
  }
  .today-line {
    fill: none;
    stroke: #f59e0b;
    stroke-width: 1.5;
    stroke-dasharray: 5 3;
  }
  .today-dot {
    fill: #f59e0b;
  }
  .axis-label {
    fill: #777;
    font-size: 10px;
  }
  .cone-footer {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem 1.2rem;
    font-size: 0.8rem;
  }
  .cone-stat {
    color: #cbd5e1;
  }
  .cone-muted {
    color: #777;
  }
  .cone-empty {
    padding: 2rem 0;
    text-align: center;
  }
</style>
