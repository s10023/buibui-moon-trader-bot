<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import {
    ColorType,
    createChart,
    type CandlestickData,
    type HistogramData,
    type IChartApi,
    type IPriceLine,
    type ISeriesApi,
    type SeriesMarker,
    type Time,
  } from "lightweight-charts";
  import type { CandleRow, SignalRow } from "../api";
  import { pricesStore, startPricesSSE, stopPricesSSE } from "../stores/prices";

  let {
    candles,
    signals,
    symbol,
    showFib = false,
  }: {
    candles: CandleRow[];
    signals: SignalRow[];
    symbol: string;
    showFib?: boolean;
  } = $props();

  let container: HTMLDivElement;
  let chart: IChartApi;
  let candleSeries: ISeriesApi<"Candlestick">;
  let volumeSeries: ISeriesApi<"Histogram">;
  let fibLines: IPriceLine[] = [];

  // ── Fib levels ───────────────────────────────────────────────────────────────

  interface FibLevel {
    ratio: number;
    color: string;
    lineStyle: number; // 0=solid, 1=dotted, 2=dashed, 3=large_dashed, 4=sparse_dotted
    lineWidth: number;
  }

  const FIB_LEVELS: FibLevel[] = [
    { ratio: 0.236, color: "#6e7681", lineStyle: 2, lineWidth: 1 },
    { ratio: 0.382, color: "#79c0ff", lineStyle: 1, lineWidth: 1 },
    { ratio: 0.5,   color: "#F59E0B", lineStyle: 0, lineWidth: 1 },
    { ratio: 0.618, color: "#F59E0B", lineStyle: 0, lineWidth: 2 },
    { ratio: 0.786, color: "#79c0ff", lineStyle: 1, lineWidth: 1 },
  ];

  function computeFibLevels(data: CandleRow[]): { price: number; level: FibLevel }[] {
    // Scan last 20 bars for swing high / swing low
    const scan = data.slice(-22); // +2 for neighbour check
    let swingHigh: number | null = null;
    let swingHighIdx = -1;
    let swingLow: number | null = null;
    let swingLowIdx = -1;

    for (let i = 1; i < scan.length - 1; i++) {
      const prev = scan[i - 1];
      const cur = scan[i];
      const next = scan[i + 1];
      if (cur.high > prev.high && cur.high > next.high) {
        if (swingHigh === null || cur.high > swingHigh) {
          swingHigh = cur.high;
          swingHighIdx = i;
        }
      }
      if (cur.low < prev.low && cur.low < next.low) {
        if (swingLow === null || cur.low < swingLow) {
          swingLow = cur.low;
          swingLowIdx = i;
        }
      }
    }

    if (swingHigh === null || swingLow === null) return [];

    const range = swingHigh - swingLow;
    if (range <= 0) return [];

    // Retracement drawn from swing_high downward regardless of direction.
    // price at ratio X = swing_high - X * range
    void swingLowIdx; // captured for potential directional logic

    return FIB_LEVELS.map((level) => ({
      price: swingHigh! - level.ratio * range,
      level,
    }));
  }

  function drawFibLines(): void {
    clearFibLines();
    if (!candleSeries || candles.length < 4) return;
    const levels = computeFibLevels(candles);
    for (const { price, level } of levels) {
      const line = candleSeries.createPriceLine({
        price,
        color: level.color,
        lineWidth: level.lineWidth as 1 | 2 | 3 | 4,
        lineStyle: level.lineStyle,
        axisLabelVisible: true,
        title: `${level.ratio.toFixed(3)}`,
      });
      fibLines.push(line);
    }
  }

  function clearFibLines(): void {
    for (const line of fibLines) {
      try { candleSeries.removePriceLine(line); } catch { /* already removed */ }
    }
    fibLines = [];
  }

  // ── Chart init ───────────────────────────────────────────────────────────────

  onMount(() => {
    chart = createChart(container, {
      width: container.clientWidth,
      height: 500,
      layout: {
        background: { type: ColorType.Solid, color: "#0d1117" },
        textColor: "#c9d1d9",
      },
      grid: {
        vertLines: { color: "#21262d" },
        horzLines: { color: "#21262d" },
      },
      crosshair: {
        vertLine: { color: "#58a6ff44", labelBackgroundColor: "#161b22" },
        horzLine: { color: "#58a6ff44", labelBackgroundColor: "#161b22" },
      },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#30363d" },
      rightPriceScale: { borderColor: "#30363d" },
    });

    candleSeries = chart.addCandlestickSeries({
      upColor: "#3fb950",
      downColor: "#f85149",
      borderUpColor: "#3fb950",
      borderDownColor: "#f85149",
      wickUpColor: "#3fb950",
      wickDownColor: "#f85149",
      priceScaleId: "right",
    });

    volumeSeries = chart.addHistogramSeries({
      color: "#30363d",
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    startPricesSSE();

    const ro = new ResizeObserver(() =>
      chart.applyOptions({ width: container.clientWidth })
    );
    ro.observe(container);
    return () => ro.disconnect();
  });

  // ── Candle data effect ────────────────────────────────────────────────────────

  $effect(() => {
    if (!candleSeries) return;
    const data: CandlestickData[] = candles.map((c) => ({
      time: (c.open_time / 1000) as Time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
    candleSeries.setData(data);

    const volData: HistogramData[] = candles.map((c) => ({
      time: (c.open_time / 1000) as Time,
      value: c.volume,
      color: c.close >= c.open ? "#3fb95044" : "#f8514944",
    }));
    volumeSeries.setData(volData);
  });

  // ── Signal markers effect ─────────────────────────────────────────────────────

  $effect(() => {
    if (!candleSeries) return;
    const markers: SeriesMarker<Time>[] = signals
      .map((s) => ({
        time: (s.open_time / 1000) as Time,
        position:
          s.direction === "long"
            ? ("belowBar" as const)
            : ("aboveBar" as const),
        color: s.direction === "long" ? "#3fb950" : "#f85149",
        shape:
          s.direction === "long"
            ? ("arrowUp" as const)
            : ("arrowDown" as const),
        text: `${s.strategy} (${s.direction})`,
        size: 1,
      }))
      .sort((a, b) => (a.time as number) - (b.time as number));
    candleSeries.setMarkers(markers);
  });

  // ── Fibonacci overlay effect ──────────────────────────────────────────────────

  $effect(() => {
    if (!candleSeries) return;
    if (showFib) {
      drawFibLines();
    } else {
      clearFibLines();
    }
  });

  // ── Live price update via SSE ─────────────────────────────────────────────────

  $effect(() => {
    const priceMap = $pricesStore;
    if (!candleSeries || candles.length === 0) return;
    const row = priceMap.get(symbol);
    if (!row) return;
    const lastPrice = parseFloat(row.last_price);
    if (isNaN(lastPrice)) return;
    const last = candles[candles.length - 1];
    candleSeries.update({
      time: (last.open_time / 1000) as Time,
      open: last.open,
      high: Math.max(last.high, lastPrice),
      low: Math.min(last.low, lastPrice),
      close: lastPrice,
    });
  });

  onDestroy(() => {
    stopPricesSSE();
    chart?.remove();
  });
</script>

<div bind:this={container} class="chart-container"></div>

<style>
  .chart-container { width: 100%; }
</style>
