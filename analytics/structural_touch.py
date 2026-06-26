"""Structural level-hold touch-decay kill-test (pure, read-only over OHLCV).

Tests the user thesis "a structural level weakens each time it is tested": does a
zone hold better (higher forward favorable excursion) on its FIRST touch than on
repeat touches? Repeat touches do not exist in the stored ledgers (the engine
writes one trade per zone; the live cooldown dedups repeats) — they are
regenerated here from raw OHLCV + `analytics/zones_lib.py` geometry.

This module is the cheap kill-test substrate (excursion-space, no entry
simulation). A robust positive escalates to a faithful per-strategy harness.

Pure functions over DataFrames; no DB / clock / network I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from analytics import zones_lib

# zones_lib `direction` ("bull"/"bear") → expected reaction on a touch.
_BIAS = {"bull": "long", "bear": "short"}

# Friendly zone-type name → iterating zones_lib extractor (all support
# max_zones=None for the full chronological history). `fib` is special-cased
# (single-current-zone extractor → walk-forward).
_EXTRACTORS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "fvg": zones_lib.extract_fvg_zones,
    "ob": zones_lib.extract_order_block_zones,
    "eqh_eql": zones_lib.extract_eqh_eql_zones,
    "bos": zones_lib.extract_bos_zones,
}


@dataclass(frozen=True)
class Zone:
    """A structural zone normalized to a price band, with its formation time.

    `bias` is the expected reaction direction on a touch: ``long`` for
    demand/support zones (price should bounce up), ``short`` for supply/
    resistance zones. Bounds and `start_ms` are fixed at formation (causal).
    """

    zone_type: str
    bias: str
    zone_low: float
    zone_high: float
    start_ms: int
    symbol: str = ""
    tf: str = ""


@dataclass(frozen=True)
class Touch:
    """One indexed touch of a zone. `touch_index` is 1-based (1 == first)."""

    touch_index: int
    bar_idx: int
    ts_ms: int


def index_touches(
    zone: Zone,
    bars: pd.DataFrame,
    *,
    min_gap_bars: int = 1,
) -> list[Touch]:
    """Index the touches of `zone` over `bars` (only bars after formation).

    A *touch* is an outside-to-inside transition: a bar whose ``[low, high]``
    range intersects the zone band, preceded by at least ``min_gap_bars`` bars
    outside the band (the very first eligible inside bar always counts).
    Contiguous inside bars are the same touch. Only bars with
    ``open_time > zone.start_ms`` are eligible (a level is touched after it
    forms — causal).
    """
    open_time = bars["open_time"].to_numpy(dtype="int64")
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)

    touches: list[Touch] = []
    prev_inside = False
    outside_run = min_gap_bars  # so the first eligible inside bar qualifies
    for i in range(len(bars)):
        if open_time[i] <= zone.start_ms:
            continue
        inside = low[i] <= zone.zone_high and high[i] >= zone.zone_low
        if inside and not prev_inside and outside_run >= min_gap_bars:
            touches.append(
                Touch(touch_index=len(touches) + 1, bar_idx=i, ts_ms=int(open_time[i]))
            )
        outside_run = 0 if inside else outside_run + 1
        prev_inside = inside
    return touches


def _atr14(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder-style ATR (rolling-mean of true range), aligned to `df` rows."""
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    prev_close = df["close"].astype(float).shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _zone_from_dict(
    z: dict[str, Any],
    atr: float,
    *,
    band_atr_frac: float,
    symbol: str = "",
    tf: str = "",
) -> Zone:
    """Normalize a zones_lib geometry dict into a banded `Zone`.

    Band zones (fvg/ob/fib) use their `zone_low`/`zone_high`; line zones
    (eqh_eql/bos carry a single `price`) wrap to ``price ± band_atr_frac*atr``.
    """
    bias = _BIAS[str(z["direction"])]
    if "zone_low" in z and "zone_high" in z:
        lo, hi = float(z["zone_low"]), float(z["zone_high"])
    else:
        price = float(z["price"])
        half = band_atr_frac * float(atr)
        lo, hi = price - half, price + half
    return Zone(
        zone_type=str(z["zone_type"]),
        bias=bias,
        zone_low=lo,
        zone_high=hi,
        start_ms=int(z["start_ms"]),
        symbol=symbol,
        tf=tf,
    )


def _fib_walk_forward(df: pd.DataFrame, step: int) -> list[dict[str, Any]]:
    """Collect historical fib golden zones causally via expanding windows.

    `extract_fib_golden_zones` returns only the single current zone, so we slide
    an expanding window (each sees only `df[:end]` — no look-ahead) and dedup by
    (start_ms, bounds). Returned in chronological start_ms order.
    """
    n = len(df)
    min_bars = 20 + 5 + 2  # swing_lookback + bos_lookback + 2 (extractor floor)
    seen: dict[tuple[int, float, float], dict[str, Any]] = {}
    for end in range(min_bars, n + 1, max(1, step)):
        for z in zones_lib.extract_fib_golden_zones(df.iloc[:end]):
            key = (
                int(z["start_ms"]),
                round(float(z["zone_low"]), 8),
                round(float(z["zone_high"]), 8),
            )
            seen.setdefault(key, z)
    return [seen[k] for k in sorted(seen)]


def extract_zones(
    df: pd.DataFrame,
    zone_type: str,
    *,
    band_atr_frac: float = 0.25,
    step: int = 1,
    symbol: str = "",
    tf: str = "",
) -> list[Zone]:
    """All historical zones of `zone_type` over `df`, normalized to `Zone`s.

    `zone_type` ∈ {fvg, ob, eqh_eql, bos, fib}. Bounds/start_ms are causal
    (fixed at formation). Line zones are wrapped to a band using the ATR at
    their formation bar (falling back to the median ATR).
    """
    if zone_type == "fib":
        dicts = _fib_walk_forward(df, step)
    else:
        # lookback=len(df) so the extractor scans the FULL history (default 100
        # would only see recent bars); max_zones=None returns every zone.
        dicts = _EXTRACTORS[zone_type](df, lookback=len(df), max_zones=None)

    atr = _atr14(df)
    open_time = df["open_time"].to_numpy(dtype="int64")
    med_atr = float(atr.median()) if len(atr) else 0.0

    zones: list[Zone] = []
    for z in dicts:
        a = med_atr
        if "price" in z:  # line zone — look up ATR at the formation bar
            idx = int(np.searchsorted(open_time, int(z["start_ms"]), side="right")) - 1
            if 0 <= idx < len(atr):
                v = float(atr.iloc[idx])
                if v > 0.0 and not np.isnan(v):
                    a = v
        zones.append(
            _zone_from_dict(z, a, band_atr_frac=band_atr_frac, symbol=symbol, tf=tf)
        )
    return zones


def touch_excursion(
    bars: pd.DataFrame,
    bar_idx: int,
    *,
    bias: str,
    atr: float,
    window: int,
) -> tuple[float, float]:
    """ATR-normalized forward (MFE, MAE) over `window` bars after a touch.

    Entry reference is the touch bar's close. Excursions are measured over bars
    STRICTLY after ``bar_idx`` (no look-ahead into the touch bar itself), up to
    ``window`` bars. Both intrabar extremes count (fixed-window convention,
    mirrors `analytics/exits/mfe_mae.py`'s "expired" branch — no exit-event
    modeling). Gross of costs. Returns (0.0, 0.0) on an empty forward window or
    non-positive ATR.
    """
    if atr <= 0.0 or bar_idx < 0:
        return 0.0, 0.0
    fwd = bars.iloc[bar_idx + 1 : bar_idx + 1 + window]
    if fwd.empty:
        return 0.0, 0.0

    entry = float(bars["close"].iloc[bar_idx])
    high = fwd["high"].to_numpy(dtype=float)
    low = fwd["low"].to_numpy(dtype=float)
    if bias == "long":
        fav = float(high.max()) - entry
        adv = entry - float(low.min())
    else:
        fav = entry - float(low.min())
        adv = float(high.max()) - entry
    return max(fav, 0.0) / atr, max(adv, 0.0) / atr


def touch_held(
    bars: pd.DataFrame,
    bar_idx: int,
    *,
    bias: str,
    atr: float,
    window: int,
    hold_thr: float,
    adv_thr: float,
) -> bool:
    """Did the level hold? True if favorable reaches ``hold_thr`` ATR before
    adverse reaches ``adv_thr`` ATR, walking bars after the touch in order.

    Adverse-first same-bar tie (conservative, mirrors `_scan_forward`). A touch
    that reaches neither threshold inside `window` counts as NOT held.
    """
    if atr <= 0.0 or bar_idx < 0:
        return False
    fwd = bars.iloc[bar_idx + 1 : bar_idx + 1 + window]
    if fwd.empty:
        return False
    entry = float(bars["close"].iloc[bar_idx])
    high = fwd["high"].to_numpy(dtype=float)
    low = fwd["low"].to_numpy(dtype=float)
    for i in range(len(fwd)):
        if bias == "long":
            adv = entry - float(low[i])
            fav = float(high[i]) - entry
        else:
            adv = float(high[i]) - entry
            fav = entry - float(low[i])
        if adv >= adv_thr * atr:  # adverse-first tie
            return False
        if fav >= hold_thr * atr:
            return True
    return False


TOUCH_TABLE_COLUMNS = [
    "symbol",
    "tf",
    "zone_type",
    "direction",
    "zone_id",
    "touch_index",
    "mfe_atr",
    "mae_atr",
    "held",
    "ts_ms",
]


def build_touch_table(
    bars_by_symbol_tf: dict[tuple[str, str], pd.DataFrame],
    zone_types: list[str],
    *,
    window: int,
    band_atr_frac: float = 0.25,
    min_gap_bars: int = 1,
    fib_step: int = 1,
    hold_thr: float = 1.0,
    adv_thr: float = 1.0,
) -> pd.DataFrame:
    """One row per (symbol, tf, zone_type, zone, touch) with forward excursion.

    For each (symbol, tf) frame, extract every historical zone per `zone_type`,
    index its touches, and record the ATR-normalized forward MFE/MAE + a `held`
    flag. The excursion ATR is taken at the touch bar (median fallback). Causal:
    excursions read only bars strictly after each touch.
    """
    rows: list[dict[str, object]] = []
    for (symbol, tf), raw in bars_by_symbol_tf.items():
        bars = raw.sort_values("open_time").reset_index(drop=True)
        if len(bars) < 3:
            continue
        atr = _atr14(bars)
        med = float(np.nanmedian(atr.to_numpy(dtype=float))) if len(atr) else 0.0
        for zt in zone_types:
            zones = extract_zones(
                bars,
                zt,
                band_atr_frac=band_atr_frac,
                step=fib_step,
                symbol=symbol,
                tf=tf,
            )
            for z in zones:
                zid = f"{symbol}:{tf}:{zt}:{z.start_ms}:{round(z.zone_low, 8)}"
                for t in index_touches(z, bars, min_gap_bars=min_gap_bars):
                    a = float(atr.iloc[t.bar_idx]) if t.bar_idx < len(atr) else med
                    if not np.isfinite(a) or a <= 0.0:
                        a = med
                    if a <= 0.0:
                        continue
                    mfe, mae = touch_excursion(
                        bars, t.bar_idx, bias=z.bias, atr=a, window=window
                    )
                    held = touch_held(
                        bars,
                        t.bar_idx,
                        bias=z.bias,
                        atr=a,
                        window=window,
                        hold_thr=hold_thr,
                        adv_thr=adv_thr,
                    )
                    rows.append(
                        {
                            "symbol": symbol,
                            "tf": tf,
                            "zone_type": zt,
                            "direction": z.bias,
                            "zone_id": zid,
                            "touch_index": t.touch_index,
                            "mfe_atr": mfe,
                            "mae_atr": mae,
                            "held": held,
                            "ts_ms": t.ts_ms,
                        }
                    )
    return pd.DataFrame(rows, columns=TOUCH_TABLE_COLUMNS)


@dataclass(frozen=True)
class TouchDecayVerdict:
    """Pre-committed first-vs-repeat-touch verdict for one (zone_type, direction)."""

    zone_type: str
    direction: str
    n_first: int
    n_repeat: int
    mfe_first: float | None
    mfe_repeat: float | None
    lift: float | None
    ci_lo: float | None
    ci_hi: float | None
    holm_p: float | None
    split_ok: bool
    decision: str  # DECAY-CONFIRMED | NO-DECAY | INSUFFICIENT


def _two_sample_lift_ci(
    a: np.ndarray,
    b: np.ndarray,
    *,
    alpha: float,
    n_boot: int,
    seed: int | None,
) -> tuple[float, float, float, float]:
    """Bootstrap (lift, ci_lo, ci_hi, two_sided_p) for mean(a) − mean(b)."""
    rng = np.random.default_rng(seed)
    lift = float(a.mean() - b.mean())
    ia = rng.integers(0, len(a), (n_boot, len(a)))
    ib = rng.integers(0, len(b), (n_boot, len(b)))
    diffs = a[ia].mean(axis=1) - b[ib].mean(axis=1)
    lo, hi = np.percentile(diffs, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    p = 2.0 * min(float(np.mean(diffs <= 0.0)), float(np.mean(diffs >= 0.0)))
    return lift, float(lo), float(hi), min(p, 1.0)


def _holm(pvalues: list[float]) -> list[float]:
    """Holm step-down adjusted p-values (generic, monotone, clipped to 1)."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adj = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, (m - rank) * pvalues[idx])
        adj[idx] = min(running, 1.0)
    return adj


def _half_lift(sub: pd.DataFrame, *, split_min: int) -> float | None:
    """Mean first-touch − repeat-touch mfe_atr for a time-split half, or None."""
    first = sub.loc[sub["touch_index"] == 1, "mfe_atr"].to_numpy(dtype=float)
    repeat = sub.loc[sub["touch_index"] >= 2, "mfe_atr"].to_numpy(dtype=float)
    if len(first) < split_min or len(repeat) < split_min:
        return None
    return float(first.mean() - repeat.mean())


def evaluate_touch_decay(
    table: pd.DataFrame,
    *,
    min_n: int = 30,
    bar: float = 0.1,
    alpha: float = 0.05,
    n_boot: int = 10_000,
    seed: int | None = 12345,
    split_min: int = 10,
) -> list[TouchDecayVerdict]:
    """Pre-committed touch-decay gate per (zone_type, direction) cell.

    DECAY-CONFIRMED iff: ``n_first >= min_n`` and ``n_repeat >= min_n``; the
    first−repeat mean-``mfe_atr`` lift's bootstrap CI lower bound exceeds
    ``+bar``; the Holm-adjusted (across the tested family) two-sided p-value is
    ``< alpha``; AND the lift is positive in BOTH time-split halves (split at
    the cell's median ts_ms — robustness, no in-sample-only optimism). Cells
    below ``min_n`` are INSUFFICIENT and excluded from the Holm family.
    """
    if table.empty:
        return []
    cells = sorted({(str(r.zone_type), str(r.direction)) for r in table.itertuples()})

    insufficient: list[TouchDecayVerdict] = []
    tested: list[dict[str, Any]] = []
    for zt, d in cells:
        sub = table[(table["zone_type"] == zt) & (table["direction"] == d)]
        first = sub.loc[sub["touch_index"] == 1, "mfe_atr"].to_numpy(dtype=float)
        repeat = sub.loc[sub["touch_index"] >= 2, "mfe_atr"].to_numpy(dtype=float)
        nf, nr = len(first), len(repeat)
        if nf < min_n or nr < min_n:
            insufficient.append(
                TouchDecayVerdict(
                    zone_type=zt,
                    direction=d,
                    n_first=nf,
                    n_repeat=nr,
                    mfe_first=float(first.mean()) if nf else None,
                    mfe_repeat=float(repeat.mean()) if nr else None,
                    lift=None,
                    ci_lo=None,
                    ci_hi=None,
                    holm_p=None,
                    split_ok=False,
                    decision="INSUFFICIENT",
                )
            )
            continue
        lift, lo, hi, p = _two_sample_lift_ci(
            first, repeat, alpha=alpha, n_boot=n_boot, seed=seed
        )
        med = float(sub["ts_ms"].median())
        early = _half_lift(sub[sub["ts_ms"] <= med], split_min=split_min)
        late = _half_lift(sub[sub["ts_ms"] > med], split_min=split_min)
        split_ok = early is not None and late is not None and early > 0 and late > 0
        tested.append(
            {
                "zone_type": zt,
                "direction": d,
                "n_first": nf,
                "n_repeat": nr,
                "mfe_first": float(first.mean()),
                "mfe_repeat": float(repeat.mean()),
                "lift": lift,
                "ci_lo": lo,
                "ci_hi": hi,
                "p": p,
                "split_ok": split_ok,
            }
        )

    holm = _holm([float(t["p"]) for t in tested]) if tested else []
    out: list[TouchDecayVerdict] = []
    for t, hp in zip(tested, holm, strict=True):
        confirmed = bool(t["ci_lo"] > bar and hp < alpha and t["split_ok"])
        out.append(
            TouchDecayVerdict(
                zone_type=str(t["zone_type"]),
                direction=str(t["direction"]),
                n_first=int(t["n_first"]),
                n_repeat=int(t["n_repeat"]),
                mfe_first=float(t["mfe_first"]),
                mfe_repeat=float(t["mfe_repeat"]),
                lift=float(t["lift"]),
                ci_lo=float(t["ci_lo"]),
                ci_hi=float(t["ci_hi"]),
                holm_p=float(hp),
                split_ok=bool(t["split_ok"]),
                decision="DECAY-CONFIRMED" if confirmed else "NO-DECAY",
            )
        )
    return out + insufficient
