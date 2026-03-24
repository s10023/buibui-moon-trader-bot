<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import {
    ColorType,
    createChart,
    type CandlestickData,
    type HistogramData,
    type IChartApi,
    type ISeriesApi,
    type LineData,
    type SeriesMarker,
    type Time,
  } from "lightweight-charts";
  import type { CandleRow, FibLevel, FundingRow, OiRow, SignalRow } from "../api";
  import { pricesStore, startPricesSSE, stopPricesSSE } from "../stores/prices";

  let {
    candles,
    signals,
    symbol,
    funding = null,
    showFunding = false,
    oi = null,
    showOI = false,
    showFib = false,
    fibLevels = null,
    showEMA20 = false,
    showEMA50 = false,
    showEMA200 = false,
    showRSI = false,
  }: {
    candles: CandleRow[];
    signals: SignalRow[];
    symbol: string;
    funding?: FundingRow[] | null;
    showFunding?: boolean;
    oi?: OiRow[] | null;
    showOI?: boolean;
    showFib?: boolean;
    fibLevels?: FibLevel[] | null;
    showEMA20?: boolean;
    showEMA50?: boolean;
    showEMA200?: boolean;
    showRSI?: boolean;
  } = $props();

  let container: HTMLDivElement;
  let chart: IChartApi;
  let candleSeries: ISeriesApi<"Candlestick">;
  let volumeSeries: ISeriesApi<"Histogram">;
  let fundingSeries: ISeriesApi<"Histogram"> | null = null;
  let oiSeries: ISeriesApi<"Line"> | null = null;
  // Each Fib level gets its own LineSeries so lines only extend rightward from
  // the swing point, not across the entire chart history.
  let fibSeries: ISeriesApi<"Line">[] = [];

  // EMA series
  let ema20Series: ISeriesApi<"Line"> | null = null;
  let ema50Series: ISeriesApi<"Line"> | null = null;
  let ema200Series: ISeriesApi<"Line"> | null = null;

  // RSI series
  let rsiSeries: ISeriesApi<"Line"> | null = null;

  // Tracks the in-progress live candle so open/high/low accumulate correctly
  // across SSE ticks instead of resetting each time.
  interface LiveCandle { openTimeSec: number; open: number; high: number; low: number; }
  let liveCandle: LiveCandle | null = null;

  // ── Indicator computation ─────────────────────────────────────────────────────

  function computeEMA(closes: number[], period: number): number[] {
    const result: number[] = [];
    if (closes.length === 0) return result;
    const k = 2 / (period + 1);
    let ema = closes[0];
    for (let i = 0; i < closes.length; i++) {
      if (i < period - 1) {
        // Not enough data yet — fill with NaN so we can skip
        result.push(NaN);
        // Accumulate SMA for seed
        if (i === 0) {
          ema = closes[0];
        } else {
          ema = ema + closes[i]; // sum accumulator
        }
      } else if (i === period - 1) {
        // Seed with SMA
        ema = (ema + closes[i]) / period;
        result.push(ema);
      } else {
        ema = closes[i] * k + ema * (1 - k);
        result.push(ema);
      }
    }
    return result;
  }

  function computeRSI(closes: number[], period: number): number[] {
    const result: number[] = [];
    if (closes.length < period + 1) return result;
    for (let i = 0; i < period; i++) result.push(NaN);

    let avgGain = 0;
    let avgLoss = 0;
    for (let i = 1; i <= period; i++) {
      const diff = closes[i] - closes[i - 1];
      if (diff >= 0) avgGain += diff;
      else avgLoss -= diff;
    }
    avgGain /= period;
    avgLoss /= period;

    const rsi = (g: number, l: number) => (l === 0 ? 100 : l === 0 && g === 0 ? 50 : 100 - 100 / (1 + g / l));
    result.push(rsi(avgGain, avgLoss));

    for (let i = period + 1; i < closes.length; i++) {
      const diff = closes[i] - closes[i - 1];
      const gain = diff > 0 ? diff : 0;
      const loss = diff < 0 ? -diff : 0;
      avgGain = (avgGain * (period - 1) + gain) / period;
      avgLoss = (avgLoss * (period - 1) + loss) / period;
      result.push(rsi(avgGain, avgLoss));
    }
    return result;
  }

  // ── Fib levels ───────────────────────────────────────────────────────────────

  interface LocalFibLevel {
    ratio: number;
    label: string;
    color: string;
    lineWidth: number;
  }

  const FIB_LEVELS: LocalFibLevel[] = [
    { ratio: 0,     label: "0",     color: "#c9d1d9", lineWidth: 1 },
    { ratio: 0.236, label: "0.236", color: "#6e7681", lineWidth: 1 },
    { ratio: 0.382, label: "0.382", color: "#79c0ff", lineWidth: 1 },
    { ratio: 0.5,   label: "0.5",   color: "#F59E0B", lineWidth: 1 },
    { ratio: 0.618, label: "0.618", color: "#F59E0B", lineWidth: 2 },
    { ratio: 0.786, label: "0.786", color: "#79c0ff", lineWidth: 1 },
    { ratio: 1,     label: "1",     color: "#c9d1d9", lineWidth: 1 },
  ];

  interface FibResult {
    price: number;
    level: LocalFibLevel;
    swingTimeSec: number;   // x-start: the earliest of swingHigh/swingLow time
    endTimeSec: number;     // x-end: last candle time + 5 intervals (right edge)
  }

  function computeFibLevels(data: CandleRow[]): FibResult[] {
    if (data.length < 4) return [];
    const scan = data.slice(-22);
    let swingHigh: number | null = null;
    let swingHighTime = 0;
    let swingLow: number | null = null;
    let swingLowTime = 0;

    for (let i = 1; i < scan.length - 1; i++) {
      const prev = scan[i - 1];
      const cur = scan[i];
      const next = scan[i + 1];
      if (cur.high > prev.high && cur.high > next.high) {
        if (swingHigh === null || cur.high > swingHigh) {
          swingHigh = cur.high;
          swingHighTime = cur.open_time;
        }
      }
      if (cur.low < prev.low && cur.low < next.low) {
        if (swingLow === null || cur.low < swingLow) {
          swingLow = cur.low;
          swingLowTime = cur.open_time;
        }
      }
    }

    if (swingHigh === null || swingLow === null) return [];
    const range = swingHigh - swingLow;
    if (range <= 0) return [];

    const last = data[data.length - 1];
    const prev = data[data.length - 2];
    const intervalMs = last.open_time - prev.open_time;
    const swingTimeSec = Math.min(swingHighTime, swingLowTime) / 1000;
    const endTimeSec = (last.open_time + 5 * intervalMs) / 1000;

    return FIB_LEVELS.map((level) => ({
      price: swingHigh! - level.ratio * range,
      level,
      swingTimeSec,
      endTimeSec,
    }));
  }

  function drawFibLines(): void {
    clearFibLines();
    if (!chart || candles.length < 4) return;

    // Prefer backend-computed levels when available; fall back to client-side.
    const backendLevels = fibLevels;
    const computed = backendLevels
      ? (() => {
          if (candles.length < 2) return [];
          const last = candles[candles.length - 1];
          const prev = candles[candles.length - 2];
          const intervalMs = last.open_time - prev.open_time;
          const swingTimeSec = (candles[0].open_time) / 1000;
          const endTimeSec = (last.open_time + 5 * intervalMs) / 1000;
          return backendLevels.map((bl) => {
            const color = bl.golden ? "#F59E0B" : (bl.label === "0.0" || bl.label === "1.0" ? "#c9d1d9" : "#79c0ff");
            const lineWidth: 1 | 2 = bl.golden && bl.label === "0.618" ? 2 : 1;
            return { price: bl.price, color, lineWidth, label: bl.label, swingTimeSec, endTimeSec };
          });
        })()
      : computeFibLevels(candles).map(({ price, level, swingTimeSec, endTimeSec }) => ({
          price, color: level.color, lineWidth: level.lineWidth as 1 | 2, label: level.label, swingTimeSec, endTimeSec,
        }));

    for (const { price, color, lineWidth, label, swingTimeSec, endTimeSec } of computed) {
      const series = chart.addLineSeries({
        color,
        lineWidth,
        priceScaleId: "right",
        lastValueVisible: true,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        title: label,
      });
      const lineData: LineData[] = [
        { time: swingTimeSec as Time, value: price },
        { time: endTimeSec as Time,   value: price },
      ];
      series.setData(lineData);
      fibSeries.push(series);
    }
  }

  function clearFibLines(): void {
    for (const s of fibSeries) {
      try { chart.removeSeries(s); } catch { /* already removed */ }
    }
    fibSeries = [];
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

    // Funding rate histogram — green/red bars below volume
    fundingSeries = chart.addHistogramSeries({
      color: "#3fb950",
      priceFormat: { type: "price", precision: 4, minMove: 0.0001 },
      priceScaleId: "funding",
    });
    chart.priceScale("funding").applyOptions({
      scaleMargins: { top: 0.92, bottom: 0 },
    });

    // Open interest line — rightmost sub-panel
    oiSeries = chart.addLineSeries({
      color: "#79c0ff",
      lineWidth: 1,
      priceFormat: { type: "volume" },
      priceScaleId: "oi",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("oi").applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    });

    // EMA overlays — on main price scale
    ema20Series = chart.addLineSeries({
      color: "#f0883e",
      lineWidth: 1,
      priceScaleId: "right",
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
      title: "EMA20",
    });

    ema50Series = chart.addLineSeries({
      color: "#58a6ff",
      lineWidth: 1,
      priceScaleId: "right",
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
      title: "EMA50",
    });

    ema200Series = chart.addLineSeries({
      color: "#bc8cff",
      lineWidth: 1,
      priceScaleId: "right",
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
      title: "EMA200",
    });

    // RSI sub-panel
    rsiSeries = chart.addLineSeries({
      color: "#e3b341",
      lineWidth: 1,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
      priceScaleId: "rsi",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("rsi").applyOptions({
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
    liveCandle = null; // reset on data reload
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

  // ── Funding rate effect ───────────────────────────────────────────────────────

  $effect(() => {
    if (!fundingSeries) return;
    if (!showFunding || !funding || funding.length === 0) {
      fundingSeries.setData([]);
      return;
    }
    fundingSeries.setData(
      funding.map((f) => ({
        time: (f.funding_time / 1000) as Time,
        value: f.funding_rate,
        color: f.funding_rate >= 0 ? "#3fb95088" : "#f8514988",
      }))
    );
  });

  // ── Open interest effect ──────────────────────────────────────────────────────

  $effect(() => {
    if (!oiSeries) return;
    if (!showOI || !oi || oi.length === 0) {
      oiSeries.setData([]);
      return;
    }
    oiSeries.setData(
      oi.map((o) => ({
        time: (o.timestamp / 1000) as Time,
        value: o.oi_usd,
      }))
    );
  });

  // ── Fibonacci overlay effect ──────────────────────────────────────────────────
  // Re-runs whenever showFib, fibLevels (backend data), or candles change.

  $effect(() => {
    if (!candleSeries) return;
    // Access fibLevels so the effect re-runs when backend data arrives.
    void fibLevels;
    void candles;
    if (showFib) {
      drawFibLines();
    } else {
      clearFibLines();
    }
  });

  // ── EMA effects ───────────────────────────────────────────────────────────────

  $effect(() => {
    if (!ema20Series) return;
    if (!showEMA20 || candles.length === 0) {
      ema20Series.setData([]);
      return;
    }
    const closes = candles.map((c) => c.close);
    const values = computeEMA(closes, 20);
    const data: LineData[] = candles
      .map((c, i) => ({ time: (c.open_time / 1000) as Time, value: values[i] }))
      .filter((d) => !isNaN(d.value));
    ema20Series.setData(data);
  });

  $effect(() => {
    if (!ema50Series) return;
    if (!showEMA50 || candles.length === 0) {
      ema50Series.setData([]);
      return;
    }
    const closes = candles.map((c) => c.close);
    const values = computeEMA(closes, 50);
    const data: LineData[] = candles
      .map((c, i) => ({ time: (c.open_time / 1000) as Time, value: values[i] }))
      .filter((d) => !isNaN(d.value));
    ema50Series.setData(data);
  });

  $effect(() => {
    if (!ema200Series) return;
    if (!showEMA200 || candles.length === 0) {
      ema200Series.setData([]);
      return;
    }
    const closes = candles.map((c) => c.close);
    const values = computeEMA(closes, 200);
    const data: LineData[] = candles
      .map((c, i) => ({ time: (c.open_time / 1000) as Time, value: values[i] }))
      .filter((d) => !isNaN(d.value));
    ema200Series.setData(data);
  });

  // ── RSI effect ────────────────────────────────────────────────────────────────

  $effect(() => {
    if (!rsiSeries) return;
    if (!showRSI || candles.length === 0) {
      rsiSeries.setData([]);
      chart.priceScale("rsi").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
      return;
    }
    const closes = candles.map((c) => c.close);
    const values = computeRSI(closes, 14);
    // values array starts at index 0 of closes; first 14 entries are NaN (warm-up)
    const data: LineData[] = candles
      .map((c, i) => ({ time: (c.open_time / 1000) as Time, value: values[i] }))
      .filter((d) => d.value !== undefined && !isNaN(d.value));
    rsiSeries.setData(data);
    // Give RSI panel a visible height
    chart.priceScale("rsi").applyOptions({ scaleMargins: { top: 0.75, bottom: 0.02 } });
  });

  // ── Live price update via SSE ─────────────────────────────────────────────────
  // Derive the current candle's open_time from the timeframe interval so that
  // if a new candle period has opened since the data was fetched, the update
  // targets the correct bar rather than patching the last closed candle.

  $effect(() => {
    const priceMap = $pricesStore;
    if (!candleSeries || candles.length < 2) return;
    const row = priceMap.get(symbol);
    if (!row) return;
    const lastPrice = parseFloat(row.last_price);
    if (isNaN(lastPrice)) return;

    const last = candles[candles.length - 1];
    const prev = candles[candles.length - 2];
    // Interval between candles in milliseconds
    const intervalMs = last.open_time - prev.open_time;
    // Snap current time to the candle boundary
    const nowMs = Date.now();
    const currentCandleOpenMs = Math.floor(nowMs / intervalMs) * intervalMs;
    const currentCandleOpenSec = (currentCandleOpenMs / 1000) as Time;

    if (currentCandleOpenMs <= last.open_time) {
      // Still within the last fetched candle — update in place
      liveCandle = null;
      candleSeries.update({
        time: (last.open_time / 1000) as Time,
        open: last.open,
        high: Math.max(last.high, lastPrice),
        low: Math.min(last.low, lastPrice),
        close: lastPrice,
      });
    } else {
      // New candle period — accumulate open/high/low across ticks
      if (!liveCandle || liveCandle.openTimeSec !== (currentCandleOpenMs / 1000)) {
        // First tick of this period — initialise
        liveCandle = { openTimeSec: currentCandleOpenMs / 1000, open: lastPrice, high: lastPrice, low: lastPrice };
      } else {
        liveCandle.high = Math.max(liveCandle.high, lastPrice);
        liveCandle.low  = Math.min(liveCandle.low,  lastPrice);
      }
      candleSeries.update({
        time: currentCandleOpenSec,
        open:  liveCandle.open,
        high:  liveCandle.high,
        low:   liveCandle.low,
        close: lastPrice,
      });
    }
  });

  onDestroy(() => {
    stopPricesSSE();
    chart?.remove();
  });
</script>

<div class="chart-wrap">
  <div bind:this={container} class="chart-container"></div>
  <img src="/buibui-logo.svg" alt="buibui" class="chart-logo" />
</div>

<style>
  .chart-wrap { position: relative; width: 100%; }
  .chart-container { width: 100%; }
  .chart-logo {
    position: absolute;
    bottom: 10px;
    left: 14px;
    height: 34px;
    opacity: 0.75;
    pointer-events: none;
    z-index: 10;
  }
  /* Hide the injected TradingView attribution */
  :global(.chart-container a) {
    display: none !important;
  }
</style>
