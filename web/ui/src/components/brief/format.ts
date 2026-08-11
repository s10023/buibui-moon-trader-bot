// Shared formatters for the Brief tab.
//
// These lived inline in Brief.svelte while it was one file. Once the page is
// split into per-card components they have to be shared, or each component
// grows its own copy and they drift — which is exactly how the tab and the
// markdown renderer would start disagreeing on the same number.

import type { BriefExternalClusterRow, BriefWeeklyState } from "../../api";

export const REGIME_LABEL: Record<string, string> = {
  trend: "Trend",
  range: "Range",
  high_vol: "High-Vol",
  unknown: "Unknown",
};

export const fmtPrice = (v: number): string =>
  v >= 1000
    ? v.toLocaleString("en-US", { maximumFractionDigits: 0 })
    : v >= 1
      ? v.toFixed(2)
      : v.toFixed(4);

export const fmtDist = (v: number): string => (v >= 0 ? `+${v.toFixed(2)}` : v.toFixed(2));

// Rounding: JS Math.round is half-up; the markdown renderer's fmt_frac is
// Python "%.0f" (half-to-even). These surfaces are independent, so a
// sub-percent half-point tie can round differently — accepted as cosmetic.
export const fmtPct = (v: number | null): string =>
  v === null ? "—" : `${Math.round(v * 100)}%`;

export const fmtR = (v: number | null): string =>
  v === null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(2);

export const signed = (x: number, dp: number): string => (x >= 0 ? "+" : "") + x.toFixed(dp);

export const hourTag = (h: number | null): string => (h === null ? "—" : `h${h}`);

// Mirrors analytics/brief/render.py::_fmt_pct — the cone clamps to p10/p90 for
// values outside the 5-percentile ladder, so the rails are unresolvable and
// must read ≤p10 / ≥p90 rather than an exact rank the cone cannot place.
export const fmtRank = (p: number): string =>
  p >= 90 ? "≥p90" : p <= 10 ? "≤p10" : `p${Math.round(p)}`;

export const asOfMyt = (ms: number): string =>
  new Date(ms).toLocaleString("en-MY", {
    timeZone: "Asia/Kuala_Lumpur",
    dateStyle: "medium",
    timeStyle: "short",
  });

const MYT_MS = 28_800_000;
const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export const mytHHMM = (ms: number): string =>
  new Date(ms + MYT_MS).toISOString().slice(11, 16);
export const mytHH = (ms: number): string => new Date(ms + MYT_MS).toISOString().slice(11, 13);
export const mytDow = (ms: number): string => DOW[(new Date(ms + MYT_MS).getUTCDay() + 6) % 7];

const PANEL_SHORT: Record<string, string> = {
  liq_heatmap: "liq",
  book_heatmap: "book",
  liq_map: "map",
};

export function panelShort(p: string): string {
  return PANEL_SHORT[p] ?? p;
}

export function extCluster(c: BriefExternalClusterRow): string {
  const lo = fmtPrice(c.price_lo);
  const hi = fmtPrice(c.price_hi);
  const band = lo === hi ? lo : `${lo}–${hi}`;
  const strength = c.intensity === "high" ? "HIGH" : c.intensity;
  return `${band} ${strength}${c.label ? " " + c.label : ""} (${fmtDist(c.dist_atr)})`;
}

export function extClusters(rows: BriefExternalClusterRow[]): string {
  return rows.length ? rows.map(extCluster).join(", ") : "none";
}

// Mirrors analytics/brief/render.py::_weekly_lines so the tab and the CLI
// cannot disagree on which moment an elapsed-hour count names.
//
// Elapsed-moment convention: h counts fully-closed bars since the Monday
// 00:00 UTC weekly open, so h=63 means 63 hours have elapsed (Wed 15:00
// UTC), not the open of the 63rd bar. h == total_bars is the right edge of
// the week and is spelled "Sun 24:00 UTC" rather than wrapping to "Mon
// 00:00" via the day division.
//
// The axis stays UTC-anchored because the week is defined by the Binance
// weekly candle; MYT rides along in the readout only, as the operator's
// working timezone. The MYT day index is computed independently, NOT
// derived from the UTC one — UTC+8 routinely lands on a different weekday
// (h=40 is Tue 16:00 UTC but Wed 00:00 MYT), and h in [160, 168] wraps into
// the FOLLOWING week's Monday while the UTC week is still open. That wrap
// collides in string form with h=0 (both read "Mon 08:00 MYT", a week
// apart); the paired UTC half always disambiguates them in context.
export function weekHourLabel(w: BriefWeeklyState): string {
  const h = w.elapsed_h;
  const hhmm = (x: number): string => `${String(x % 24).padStart(2, "0")}:00`;
  const utc = h >= w.total_bars ? "Sun 24:00 UTC" : `${DOW[Math.floor(h / 24)]} ${hhmm(h)} UTC`;
  const myt = (h + 8) % w.total_bars;
  return `${utc} · ${DOW[Math.floor(myt / 24)]} ${hhmm(myt)} MYT`;
}

// Proximity, 0..1, for the ladder gauge. Levels within ~3 ATR carry almost all
// the decision weight, so the gauge saturates there rather than scaling to the
// furthest level on screen — an absolute scale keeps the bar comparable across
// symbols and across refreshes, which a relative one would not.
const GAUGE_SATURATION_ATR = 3;

export function proximity(distAtr: number): number {
  const d = Math.min(Math.abs(distAtr), GAUGE_SATURATION_ATR);
  return 1 - d / GAUGE_SATURATION_ATR;
}
