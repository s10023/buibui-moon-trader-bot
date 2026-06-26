"""Faithful per-strategy structural entry-simulation harness.

Escalation from the structural level-hold touch-decay kill-test
(``docs/audits/2026-06-26-structural-level-hold-touch-decay.md``). That audit
found first touches of structural zones run further favorably than repeat
touches **in excursion space** (``mfe_atr``, gross of costs) — but excursion is
not realized R through an entry / stop / TP. This module converts the
excursion premium into **cost-netted realized avg_r** with real entries/stops
and a pre-committed de-biased BUILD / NO-EDGE gate.

Design (pure, read-only, additive — no schema / golden change):

* Touch geometry is reused verbatim from :mod:`analytics.structural_touch`
  (``extract_zones`` / ``index_touches`` / ``Zone`` / ``Touch``).
* Realized-R resolution is delegated to the **production backtest engine**
  (:func:`analytics.backtest.engine.run_backtest`) so the cost model
  (``net_R = raw − fee − slippage − funding``), the next-bar-open entry, and
  the SL/TP candle scan match live exactly — zero drift. Each indexed touch
  becomes one synthetic signal row (``open_time`` = touch bar, structural
  ``sl_price`` from the sl-model, ``direction`` = the zone's reaction bias).
* The stop model is computed **here** (not inside the engine) so the engine
  never widens ``sl_price`` — keeping ``Trade.sl_price`` identical to the input
  and the touch↔trade merge on ``(open_time, direction, sl_price)`` exact.
* The de-biased gate reuses :mod:`analytics.audit_guard` (bootstrap CI + Holm)
  and :mod:`analytics.research_guards` (DSR / PBO / MinTRL) — the same stack as
  ``tools/reference_level_proximity_audit.py``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from analytics import audit_guard
from analytics.backtest.engine import Trade, run_backtest
from analytics.research_guards import (
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
)
from analytics.structural_touch import (
    Touch,
    Zone,
    _atr14,
    _holm,
    _two_sample_lift_ci,
    extract_zones,
    index_touches,
)

_SIGNAL_COLUMNS = ["open_time", "direction", "sl_price", "touch_index", "zone_id"]


def _zone_id(zone: Zone) -> str:
    return f"{zone.zone_type}:{zone.start_ms}:{zone.zone_low:.8f}:{zone.zone_high:.8f}"


def _gross_r(trade: Trade) -> float | None:
    """Raw R (gross of fees / slippage / funding) for a closed trade."""
    if trade.exit_price is None:
        return None
    risk = abs(trade.entry_price - trade.sl_price)
    if risk == 0.0:
        return None
    if trade.direction == "long":
        return (trade.exit_price - trade.entry_price) / risk
    return (trade.entry_price - trade.exit_price) / risk


SL_MODELS: tuple[str, ...] = ("structural", "atr_floor", "fixed_atr")

REALIZED_TABLE_COLUMNS: list[str] = [
    "symbol",
    "tf",
    "zone_type",
    "direction",
    "zone_id",
    "touch_index",
    "tp_r",
    "sl_model",
    "pnl_r",
    "pnl_r_gross",
    "ts_ms",
]


def touch_sl_price(
    zone: Zone,
    *,
    entry_ref: float,
    atr: float,
    sl_model: str,
    atr_floor_frac: float = 0.5,
    atr_mult: float = 1.0,
) -> float:
    """Stop price for a touch of ``zone`` under ``sl_model``.

    ``entry_ref`` is the touch bar's close (the entry proxy used for the
    ATR-relative models; the engine enters at the next bar's open). ``atr`` is
    the ATR at the touch bar.

    * ``structural`` — the far band edge: ``zone_low`` for a long (demand) zone,
      ``zone_high`` for a short (supply) zone. The detectors' own stop.
    * ``atr_floor`` — the structural edge, widened to at least ``atr_floor_frac``
      × ATR of risk from ``entry_ref`` (a conservative minimum that defuses the
      tight-stop wick-out inflation when entry lands on the band).
    * ``fixed_atr`` — a pure ``atr_mult`` × ATR stop from ``entry_ref``,
      ignoring the structural edge.
    """
    is_long = zone.bias == "long"
    far_edge = zone.zone_low if is_long else zone.zone_high

    if sl_model == "structural":
        return far_edge
    if sl_model == "fixed_atr":
        dist = atr_mult * atr
        return entry_ref - dist if is_long else entry_ref + dist
    if sl_model == "atr_floor":
        floor = atr_floor_frac * atr
        if is_long:
            return min(far_edge, entry_ref - floor)
        return max(far_edge, entry_ref + floor)
    raise ValueError(f"unknown sl_model: {sl_model!r}")


def build_touch_signals(
    zone: Zone,
    touches: Sequence[Touch],
    bars: pd.DataFrame,
    *,
    sl_model: str,
    atr_floor_frac: float = 0.5,
    atr_mult: float = 1.0,
) -> pd.DataFrame:
    """One synthetic engine signal row per touch (see module docstring)."""
    if not touches:
        return pd.DataFrame({c: [] for c in _SIGNAL_COLUMNS})

    closes = bars["close"].to_numpy(dtype=float)
    open_time = bars["open_time"].to_numpy(dtype="int64")
    atr = _atr14(bars).to_numpy(dtype=float)
    med_atr = float(pd.Series(atr).median())
    zid = _zone_id(zone)

    rows: list[dict[str, Any]] = []
    for t in touches:
        i = t.bar_idx
        a = float(atr[i]) if 0 <= i < len(atr) and atr[i] > 0.0 else med_atr
        sl = touch_sl_price(
            zone,
            entry_ref=float(closes[i]),
            atr=a,
            sl_model=sl_model,
            atr_floor_frac=atr_floor_frac,
            atr_mult=atr_mult,
        )
        rows.append(
            {
                "open_time": int(open_time[i]),
                "direction": zone.bias,
                "sl_price": float(sl),
                "touch_index": int(t.touch_index),
                "zone_id": zid,
            }
        )
    return pd.DataFrame(rows, columns=_SIGNAL_COLUMNS)


def resolve_touch_trades(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    *,
    symbol: str,
    tf: str,
    tp_r: float,
    fee_pct: float = 0.0,
    slippage_pct: float = 0.0,
    funding_series: pd.Series | None = None,
) -> pd.DataFrame:
    """Resolve synthetic touch signals to closed trades via ``run_backtest``.

    Returns one row per *closed* trade with ``open_time`` (= the signal candle),
    ``direction``, ``sl_price``, net ``pnl_r`` and gross ``pnl_r_gross``. Signals
    are deduped to unique ``(open_time, direction, sl_price)`` before resolution
    (identical keys ⇒ identical trade ⇒ identical R); the engine's SL widening
    is disabled (``min_sl_pct=0``, ``atr_sl_floor=False``) so ``Trade.sl_price``
    equals the input and the touch↔trade merge stays exact.
    """
    cols = ["open_time", "direction", "sl_price"]
    out_cols = ["open_time", "direction", "sl_price", "pnl_r", "pnl_r_gross"]
    if signals.empty:
        return pd.DataFrame({c: [] for c in out_cols})

    dedup = (
        signals[cols].drop_duplicates().sort_values("open_time").reset_index(drop=True)
    )
    result = run_backtest(
        bars,
        dedup,
        symbol=symbol,
        timeframe=tf,
        strategy="structural_entry",
        tp_r=tp_r,
        fee_pct=fee_pct,
        slippage_pct=slippage_pct,
        funding_series=funding_series,
        min_sl_pct=0.0,
        atr_sl_floor=False,
    )

    rows: list[dict[str, Any]] = []
    for t in result.closed_trades:
        net = t.pnl_r
        gross = _gross_r(t)
        if net is None or gross is None:
            continue
        rows.append(
            {
                "open_time": int(t.signal_time),
                "direction": str(t.direction),
                "sl_price": float(t.sl_price),
                "pnl_r": float(net),
                "pnl_r_gross": float(gross),
            }
        )
    return pd.DataFrame(rows, columns=out_cols)


def _merge_touches_to_trades(
    touch_signals: pd.DataFrame, trades: pd.DataFrame
) -> pd.DataFrame:
    """Attach realized R to every touch by an exact ``(open_time, direction,
    sl_price)`` join. Touches whose trade never closed (no match) are dropped.
    """
    out_cols = ["zone_id", "touch_index", "direction", "ts_ms", "pnl_r", "pnl_r_gross"]
    if touch_signals.empty or trades.empty:
        return pd.DataFrame({c: [] for c in out_cols})

    left = touch_signals.copy()
    right = trades.copy()
    left["_slk"] = left["sl_price"].round(8)
    right["_slk"] = right["sl_price"].round(8)
    merged = left.merge(
        right[["open_time", "direction", "_slk", "pnl_r", "pnl_r_gross"]],
        on=["open_time", "direction", "_slk"],
        how="inner",
    )
    merged = merged.rename(columns={"open_time": "ts_ms"})
    return merged[out_cols].reset_index(drop=True)


def extract_zone_touches(
    bars: pd.DataFrame,
    zone_type: str,
    *,
    symbol: str = "",
    tf: str = "",
    band_atr_frac: float = 0.25,
    min_gap_bars: int = 1,
    fib_step: int = 5,
) -> list[tuple[Zone, list[Touch]]]:
    """All zones of ``zone_type`` paired with their indexed touches (causal)."""
    step = fib_step if zone_type == "fib" else 1
    zones = extract_zones(
        bars, zone_type, band_atr_frac=band_atr_frac, step=step, symbol=symbol, tf=tf
    )
    out: list[tuple[Zone, list[Touch]]] = []
    for zone in zones:
        touches = index_touches(zone, bars, min_gap_bars=min_gap_bars)
        if touches:
            out.append((zone, touches))
    return out


def simulate_cell(
    bars: pd.DataFrame,
    zone_type: str,
    *,
    symbol: str,
    tf: str,
    tp_r: float,
    sl_model: str,
    fee_pct: float = 0.0,
    slippage_pct: float = 0.0,
    funding_series: pd.Series | None = None,
    band_atr_frac: float = 0.25,
    min_gap_bars: int = 1,
    fib_step: int = 5,
    atr_floor_frac: float = 0.5,
    atr_mult: float = 1.0,
    zone_touches: Sequence[tuple[Zone, list[Touch]]] | None = None,
) -> pd.DataFrame:
    """Per-touch realized-R rows for one (symbol, tf, zone_type, tp_r, sl_model).

    ``zone_touches`` (zone, touches) pairs may be supplied to skip re-extraction
    across a tp_r × sl_model grid; when ``None`` they are extracted from ``bars``.
    """
    empty = pd.DataFrame({c: [] for c in REALIZED_TABLE_COLUMNS})
    if zone_touches is None:
        zone_touches = extract_zone_touches(
            bars,
            zone_type,
            symbol=symbol,
            tf=tf,
            band_atr_frac=band_atr_frac,
            min_gap_bars=min_gap_bars,
            fib_step=fib_step,
        )
    if not zone_touches:
        return empty

    sig_frames = [
        build_touch_signals(
            zone,
            touches,
            bars,
            sl_model=sl_model,
            atr_floor_frac=atr_floor_frac,
            atr_mult=atr_mult,
        )
        for zone, touches in zone_touches
    ]
    touch_signals = pd.concat(sig_frames, ignore_index=True)
    if touch_signals.empty:
        return empty

    trades = resolve_touch_trades(
        bars,
        touch_signals,
        symbol=symbol,
        tf=tf,
        tp_r=tp_r,
        fee_pct=fee_pct,
        slippage_pct=slippage_pct,
        funding_series=funding_series,
    )
    merged = _merge_touches_to_trades(touch_signals, trades)
    if merged.empty:
        return empty

    merged["symbol"] = symbol
    merged["tf"] = tf
    merged["zone_type"] = zone_type
    merged["tp_r"] = float(tp_r)
    merged["sl_model"] = sl_model
    return merged[REALIZED_TABLE_COLUMNS].reset_index(drop=True)


def build_realized_table(
    bars_by_symbol_tf: Mapping[tuple[str, str], pd.DataFrame],
    zone_types: Sequence[str],
    *,
    tp_r_grid: Sequence[float],
    sl_models: Sequence[str],
    fee_pct: float = 0.0,
    slippage_pct: float = 0.0,
    funding_by_symbol: Mapping[str, pd.Series] | None = None,
    band_atr_frac: float = 0.25,
    min_gap_bars: int = 1,
    fib_step: int = 5,
    atr_floor_frac: float = 0.5,
    atr_mult: float = 1.0,
) -> pd.DataFrame:
    """Concatenated per-touch realized-R rows over the full symbol × tf × cell grid.

    Zones are extracted once per (symbol, tf, zone_type) and reused across the
    tp_r × sl_model grid (the slow extractor runs once, not per param).
    """
    frames: list[pd.DataFrame] = []
    for (symbol, tf), bars in bars_by_symbol_tf.items():
        funding = funding_by_symbol.get(symbol) if funding_by_symbol else None
        for zone_type in zone_types:
            zt = extract_zone_touches(
                bars,
                zone_type,
                symbol=symbol,
                tf=tf,
                band_atr_frac=band_atr_frac,
                min_gap_bars=min_gap_bars,
                fib_step=fib_step,
            )
            if not zt:
                continue
            for tp_r in tp_r_grid:
                for sl_model in sl_models:
                    df = simulate_cell(
                        bars,
                        zone_type,
                        symbol=symbol,
                        tf=tf,
                        tp_r=tp_r,
                        sl_model=sl_model,
                        fee_pct=fee_pct,
                        slippage_pct=slippage_pct,
                        funding_series=funding,
                        atr_floor_frac=atr_floor_frac,
                        atr_mult=atr_mult,
                        zone_touches=zt,
                    )
                    if not df.empty:
                        frames.append(df)
    if not frames:
        return pd.DataFrame({c: [] for c in REALIZED_TABLE_COLUMNS})
    return pd.concat(frames, ignore_index=True)


@dataclass(frozen=True)
class StructuralBuildVerdict:
    """Pre-committed BUILD / NO-EDGE / INSUFFICIENT verdict for one cell."""

    zone_type: str
    direction: str
    n_first: int
    n_repeat: int
    first_avg_r: float | None
    repeat_avg_r: float | None
    boot_lo: float | None
    boot_hi: float | None
    holm_p: float | None
    mintrl: float | None
    dsr: float | None
    pbo: float | None
    decay_lift: float | None
    decay_lo: float | None
    decay_holm_p: float | None
    time_split_ok: bool
    decision: str  # BUILD | NO-EDGE | INSUFFICIENT


def evaluate_build(
    table: pd.DataFrame,
    *,
    headline_tp_r: float,
    headline_sl_model: str,
    headline_tf: str = "1d",
    min_n: int = 30,
    bar: float = 0.0,
    alpha: float = 0.05,
    n_boot: int = 10_000,
    seed: int | None = 12345,
    split_min: int = 10,
) -> list[StructuralBuildVerdict]:
    """Pre-committed BUILD gate per (zone_type × direction) cell.

    The decision keys on a **pre-committed headline config**
    (``headline_tp_r`` × ``headline_sl_model`` × ``headline_tf``) — no best-of
    selection. BUILD requires, on that config's first-touch (``touch_index==1``)
    net realized R:

    * the block-bootstrap CI lower bound clears ``+bar`` and the Holm-adjusted
      p-value (across the (zone_type × direction) family) is ``< alpha``
      (both via :func:`audit_guard.evaluate_audit_cells`);
    * ``n_first >= MinTRL(0.95)``;
    * DSR ``>= 0.95`` and PBO ``<= 0.5`` over the tp_r × sl_model trial family at
      ``headline_tf`` — these bind only when computable (a ≥2-trial / ≥28-obs
      family), else they do not block (NaN-skip, mirroring the forecast report).

    The first−repeat decay lift (+ time-split) is computed and reported as
    secondary corroboration; it does not gate the decision. Cells below
    ``min_n`` first touches are INSUFFICIENT and excluded from the Holm family.
    """
    if table.empty:
        return []

    head = table[
        (table["tp_r"] == headline_tp_r)
        & (table["sl_model"] == headline_sl_model)
        & (table["tf"] == headline_tf)
    ]
    cells = sorted(
        {
            (str(zt), str(d))
            for zt, d in zip(table["zone_type"], table["direction"], strict=True)
        }
    )

    first_by_cell: dict[tuple[str, str], np.ndarray] = {}
    repeat_by_cell: dict[tuple[str, str], np.ndarray] = {}
    sub_by_cell: dict[tuple[str, str], pd.DataFrame] = {}
    for zt, d in cells:
        sub = head[(head["zone_type"] == zt) & (head["direction"] == d)]
        sub_by_cell[(zt, d)] = sub
        first_by_cell[(zt, d)] = sub.loc[sub["touch_index"] == 1, "pnl_r"].to_numpy(
            float
        )
        repeat_by_cell[(zt, d)] = sub.loc[sub["touch_index"] >= 2, "pnl_r"].to_numpy(
            float
        )

    audit_cells = [
        audit_guard.AuditCell(label=f"{zt}/{d}", supp_r=first_by_cell[(zt, d)].tolist())
        for zt, d in cells
    ]
    cvs = audit_guard.evaluate_audit_cells(
        audit_cells, bar=bar, alpha=alpha, min_n=min_n, n_boot=n_boot, seed=seed
    )

    # Secondary decay leg: raw two-sample p per eligible cell, Holm-adjusted.
    decay_raw: dict[tuple[str, str], tuple[float, float, float, bool]] = {}
    decay_ps: list[float] = []
    decay_keys: list[tuple[str, str]] = []
    for zt, d in cells:
        first, repeat = first_by_cell[(zt, d)], repeat_by_cell[(zt, d)]
        if first.shape[0] < min_n or repeat.shape[0] < min_n:
            continue
        lift, lo, _hi, p = _two_sample_lift_ci(
            first, repeat, alpha=alpha, n_boot=n_boot, seed=seed
        )
        split_ok = _decay_split_ok(sub_by_cell[(zt, d)], split_min=split_min)
        decay_raw[(zt, d)] = (lift, lo, p, split_ok)
        decay_ps.append(p)
        decay_keys.append((zt, d))
    decay_adj = dict(zip(decay_keys, _holm(decay_ps), strict=True)) if decay_ps else {}

    out: list[StructuralBuildVerdict] = []
    for (zt, d), cv in zip(cells, cvs, strict=True):
        first, repeat = first_by_cell[(zt, d)], repeat_by_cell[(zt, d)]
        nf, nr = first.shape[0], repeat.shape[0]
        first_avg = float(first.mean()) if nf else None
        repeat_avg = float(repeat.mean()) if nr else None

        decay_lift: float | None = None
        decay_lo: float | None = None
        decay_holm: float | None = None
        split_ok = False
        if (zt, d) in decay_raw:
            decay_lift, decay_lo, _p, split_ok = decay_raw[(zt, d)]
            decay_holm = decay_adj[(zt, d)]

        if nf < min_n:
            out.append(
                StructuralBuildVerdict(
                    zt,
                    d,
                    nf,
                    nr,
                    first_avg,
                    repeat_avg,
                    cv.ci_lo,
                    cv.ci_hi,
                    cv.adj_pvalue,
                    None,
                    None,
                    None,
                    decay_lift,
                    decay_lo,
                    decay_holm,
                    split_ok,
                    "INSUFFICIENT",
                )
            )
            continue

        abs_pass = cv.decision in (
            audit_guard.DECISION_DISABLE,
            audit_guard.DECISION_CONCENTRATE,
        )
        sr = _per_trade_sharpe(first)
        mintrl = (
            min_track_record_length(sr, target_sr=0.0, confidence=0.95)
            if sr > 0.0
            else None
        )
        mintrl_pass = mintrl is not None and np.isfinite(mintrl) and nf >= mintrl
        dsr, pbo = _family_dsr_pbo(
            table,
            zt,
            d,
            headline_tf=headline_tf,
            sr_headline=sr,
            n_headline=nf,
            seed=seed,
        )
        dsr_pass = dsr is None or dsr >= 0.95
        pbo_pass = pbo is None or pbo <= 0.5

        build = abs_pass and mintrl_pass and dsr_pass and pbo_pass
        out.append(
            StructuralBuildVerdict(
                zt,
                d,
                nf,
                nr,
                first_avg,
                repeat_avg,
                cv.ci_lo,
                cv.ci_hi,
                cv.adj_pvalue,
                mintrl,
                dsr,
                pbo,
                decay_lift,
                decay_lo,
                decay_holm,
                split_ok,
                "BUILD" if build else "NO-EDGE",
            )
        )
    return out


def _per_trade_sharpe(arr: np.ndarray) -> float:
    if arr.shape[0] < 2:
        return 0.0
    sd = float(np.std(arr, ddof=1))
    return 0.0 if sd == 0.0 else float(np.mean(arr)) / sd


def _decay_split_ok(sub: pd.DataFrame, *, split_min: int) -> bool:
    """First−repeat mean-R lift positive in BOTH early/late time-split halves."""
    if sub.empty:
        return False
    med = float(sub["ts_ms"].median())
    for half in (sub[sub["ts_ms"] <= med], sub[sub["ts_ms"] > med]):
        first = half.loc[half["touch_index"] == 1, "pnl_r"].to_numpy(float)
        repeat = half.loc[half["touch_index"] >= 2, "pnl_r"].to_numpy(float)
        if first.shape[0] < split_min or repeat.shape[0] < split_min:
            return False
        if float(first.mean() - repeat.mean()) <= 0.0:
            return False
    return True


def _family_dsr_pbo(
    table: pd.DataFrame,
    zone_type: str,
    direction: str,
    *,
    headline_tf: str,
    sr_headline: float,
    n_headline: int,
    seed: int | None,
) -> tuple[float | None, float | None]:
    """DSR / PBO over the tp_r × sl_model trial family for one cell (headline tf).

    DSR deflates the headline first-touch Sharpe against the family's
    expected-max; PBO is CSCV over the per-trial first-touch R matrix (forecast
    convention: truncate trials to the shortest, column-stack). Both are
    ``None`` when uncomputable (no positive headline Sharpe / <2 trials / <28
    aligned observations).
    """
    cell = table[
        (table["zone_type"] == zone_type)
        & (table["direction"] == direction)
        & (table["tf"] == headline_tf)
        & (table["touch_index"] == 1)
    ]
    trial_arrays: list[np.ndarray] = []
    trial_srs: list[float] = []
    for _key, grp in cell.groupby(["tp_r", "sl_model"]):
        arr = grp["pnl_r"].to_numpy(float)
        if arr.shape[0] >= 2:
            trial_arrays.append(arr)
            trial_srs.append(_per_trade_sharpe(arr))

    dsr: float | None = None
    if sr_headline > 0.0 and trial_srs:
        dsr = deflated_sharpe_ratio(sr_headline, n_headline, trial_srs=trial_srs)

    pbo: float | None = None
    min_len = min((a.shape[0] for a in trial_arrays), default=0)
    if len(trial_arrays) >= 2 and min_len >= 28:
        mat = np.column_stack([a[-min_len:] for a in trial_arrays])
        pbo = cscv_pbo(mat).pbo
    return dsr, pbo
