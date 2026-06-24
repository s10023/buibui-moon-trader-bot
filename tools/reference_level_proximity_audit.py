#!/usr/bin/env python
"""Reference-level proximity audit (read-only).

Measures whether proximity to a calendar reference level (period opens, the
Monday range, previous-day / previous-week extremes) lifts realized avg_r —
before committing to building a ``reference_level`` detector. Tags each already
-fired signal by distance-to-nearest-level (ATR-normalized) and a sweep flag,
splits avg_r near-vs-far **within direction**, and returns a pre-committed
**BUILD / NO-EDGE / INSUFFICIENT** verdict for the journal hypothesis (sweep +
reclaim/reject at prior-period extremes).

The verdict reuses ``analytics.audit_guard.evaluate_audit_cells`` for the
de-biased absolute test (block-bootstrap CI clearing +bar AND a Holm
multiple-testing haircut), and adds a seeded two-sample bootstrap for the
near-vs-far lift. LIVE ``signal_alert_outcomes`` (OOS, net of costs) is the
gate; ``backtest_trades`` (IS) is corroboration only.

Read-only — no DB writes, no engine change. See
``docs/superpowers/specs`` plan / ``docs/audits/2026-06-24-reference-level-proximity.md``.

Run:
    PYTHONPATH=. poetry run python tools/reference_level_proximity_audit.py \
        --source both --min-n 30
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import numpy.typing as npt
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from analytics import audit_guard  # noqa: E402
from analytics.reference_levels import LEVEL_NAMES, compute_levels_table  # noqa: E402
from analytics.store import DEFAULT_DB_PATH  # noqa: E402
from analytics.store.market_data import get_ohlcv  # noqa: E402

PRIMARY_LABELS = (
    "P1: long sweep+reclaim @ PDL/PWL",
    "P2: short sweep+reject @ PDH/PWH",
)
NEAR_BANDS = ("<=0.25", "<=0.5")
DEFAULT_OUT = REPO_ROOT / "docs" / "audits" / "2026-06-24-reference-level-proximity.md"

_TF_MS = {
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
    "1w": 7 * 24 * 60 * 60_000,
}


def _tf_ms(tf: str) -> int:
    return _TF_MS.get(tf, 60 * 60_000)


# --------------------------------------------------------------------------- #
# pure helpers                                                                 #
# --------------------------------------------------------------------------- #


def _band_of(dist_atr: pd.Series, bands: Sequence[float]) -> npt.NDArray[np.object_]:
    """Map an ATR-normalized distance series to band labels.

    ``bands`` are the three thresholds (e.g. ``(0.25, 0.5, 1.0)``). NaN → ``n/a``.
    """
    d = dist_atr.to_numpy(dtype=float)
    out = np.full(d.shape[0], ">1.0", dtype=object)
    out[d <= bands[2]] = "<=1.0"
    out[d <= bands[1]] = "<=0.5"
    out[d <= bands[0]] = "<=0.25"
    out[np.isnan(d)] = "n/a"
    return out


def _atr14_series(t: pd.DataFrame, period: int = 14) -> pd.Series:
    """Vectorized ATR14 matching ``analytics.backtest.engine._compute_atr14``.

    Simple mean of True Range over the trailing ``period`` candles; the first
    bar has no prior close so its ATR is NaN (parity with the engine's
    ``idx < 1 -> None``). Equal to the engine's per-index value once the window
    is full (idx >= period).
    """
    high = t["high"].astype(float)
    low = t["low"].astype(float)
    prev_close = t["close"].astype(float).shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    tr.iloc[0] = np.nan
    return tr.rolling(period, min_periods=1).mean()


def _two_sample_lift_ci(
    near: npt.NDArray[np.float64] | Sequence[float],
    far: npt.NDArray[np.float64] | Sequence[float],
    *,
    alpha: float = 0.05,
    n_boot: int = 10_000,
    seed: int | None = 12345,
) -> tuple[float, float]:
    """Seeded two-sample bootstrap CI for ``mean(near) - mean(far)``.

    Independent i.i.d. resamples of each cohort; percentile CI of the
    difference of means. ``(nan, nan)`` when either cohort has < 2 rows.
    """
    a = np.asarray(near, dtype=np.float64)
    b = np.asarray(far, dtype=np.float64)
    if a.shape[0] < 2 or b.shape[0] < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    diffs = np.empty(n_boot, dtype=np.float64)
    for i in range(n_boot):
        ra = rng.integers(0, a.shape[0], a.shape[0])
        rb = rng.integers(0, b.shape[0], b.shape[0])
        diffs[i] = a[ra].mean() - b[rb].mean()
    lo = float(np.quantile(diffs, alpha / 2.0))
    hi = float(np.quantile(diffs, 1.0 - alpha / 2.0))
    return (lo, hi)


# --------------------------------------------------------------------------- #
# source normalization                                                         #
# --------------------------------------------------------------------------- #


def normalize_live(df: pd.DataFrame) -> pd.DataFrame:
    """``signal_alert_outcomes`` rows → the common entry frame."""
    out = pd.DataFrame(
        {
            "symbol": df["symbol"],
            "tf": df["tf"],
            "direction": df["direction"],
            "entry_price": df["entry_price"],
            "ts_ms": df["candle_ts_ms"],
            "r": df["outcome_r"],
        }
    )
    return out.dropna(subset=["entry_price", "ts_ms", "r"]).reset_index(drop=True)


def normalize_backtest(df: pd.DataFrame) -> pd.DataFrame:
    """``backtest_trades`` rows → the common entry frame."""
    out = pd.DataFrame(
        {
            "symbol": df["symbol"],
            "tf": df["timeframe"],
            "direction": df["direction"],
            "entry_price": df["entry_price"],
            "ts_ms": df["signal_time"],
            "r": df["pnl_r"],
        }
    )
    return out.dropna(subset=["entry_price", "ts_ms", "r"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# tagging                                                                       #
# --------------------------------------------------------------------------- #


def tag_entries(
    entries: pd.DataFrame,
    daily_by_symbol: dict[str, pd.DataFrame],
    tf_by_symbol: dict[tuple[str, str], pd.DataFrame],
    *,
    sweep_lookback: int = 3,
    bands: tuple[float, float, float] = (0.25, 0.5, 1.0),
) -> pd.DataFrame:
    """Tag each entry with proximity band, nearest level, and the primary sweep.

    Adds columns: ``near_name`` / ``near_dist_atr`` / ``near_band`` (nearest of
    all 9 levels), and ``prim_dist_atr`` / ``prim_band`` / ``prim_sweep`` (the
    direction-specific prior-period extreme — PDL/PWL for longs, PDH/PWH for
    shorts — and whether price swept-and-reclaimed/rejected it).
    """
    if entries.empty:
        return entries.copy()

    parts: list[pd.DataFrame] = []
    for symbol, sub in entries.groupby("symbol", sort=False):
        daily = daily_by_symbol.get(str(symbol))
        if daily is None or daily.empty:
            continue
        table = compute_levels_table(daily)
        sub = sub.copy()
        dates = pd.to_datetime(sub["ts_ms"], unit="ms", utc=True).dt.normalize()
        lv = table.reindex(dates)
        for col in LEVEL_NAMES:
            sub[col] = lv[col].to_numpy()
        for feat_col in ("_atr", "_close", "_rml", "_rmh"):
            sub[feat_col] = np.nan
        for tf, grp in sub.groupby("tf", sort=False):
            tf_df = tf_by_symbol.get((str(symbol), str(tf)))
            if tf_df is None or tf_df.empty:
                continue
            t = tf_df.sort_values("open_time").reset_index(drop=True)
            feat = pd.DataFrame(
                {
                    "_atr": _atr14_series(t).to_numpy(),
                    "_close": t["close"].astype(float).to_numpy(),
                    "_rml": t["low"]
                    .astype(float)
                    .rolling(sweep_lookback, min_periods=1)
                    .min()
                    .to_numpy(),
                    "_rmh": t["high"]
                    .astype(float)
                    .rolling(sweep_lookback, min_periods=1)
                    .max()
                    .to_numpy(),
                },
                index=t["open_time"].to_numpy(),
            )
            mapped = feat.reindex(grp["ts_ms"].to_numpy())
            for feat_col in ("_atr", "_close", "_rml", "_rmh"):
                sub.loc[grp.index, feat_col] = mapped[feat_col].to_numpy()
        parts.append(sub)

    if not parts:
        return entries.iloc[0:0].copy()
    tagged = pd.concat(parts)

    n = len(tagged)
    cols = list(LEVEL_NAMES)
    lvl_vals = tagged[cols].to_numpy(dtype=float)
    price = tagged["entry_price"].to_numpy(dtype=float)[:, None]
    dist = np.abs(lvl_vals - price)
    dist_filled = np.where(np.isnan(dist), np.inf, dist)
    atr = tagged["_atr"].to_numpy(dtype=float)
    rows = np.arange(n)

    near_idx = np.argmin(dist_filled, axis=1)
    near_min = dist_filled[rows, near_idx]
    near_min = np.where(np.isfinite(near_min), near_min, np.nan)
    names = np.asarray(cols, dtype=object)[near_idx]
    tagged["near_name"] = np.where(np.isnan(near_min), "", names)
    with np.errstate(invalid="ignore", divide="ignore"):
        tagged["near_dist_atr"] = near_min / atr
    tagged["near_band"] = _band_of(tagged["near_dist_atr"], bands)

    def _nearest_pair(
        a: int, b: int
    ) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
        pair = dist_filled[:, [a, b]]
        arg = np.argmin(pair, axis=1)
        return pair[rows, arg], lvl_vals[:, [a, b]][rows, arg]

    long_mask = tagged["direction"].to_numpy() == "long"
    long_min, long_lvl = _nearest_pair(cols.index("PDL"), cols.index("PWL"))
    short_min, short_lvl = _nearest_pair(cols.index("PDH"), cols.index("PWH"))
    prim_min = np.where(long_mask, long_min, short_min)
    prim_lvl = np.where(long_mask, long_lvl, short_lvl)
    prim_min = np.where(np.isfinite(prim_min), prim_min, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        tagged["prim_dist_atr"] = prim_min / atr
    tagged["prim_band"] = _band_of(tagged["prim_dist_atr"], bands)

    close = tagged["_close"].to_numpy(dtype=float)
    rml = tagged["_rml"].to_numpy(dtype=float)
    rmh = tagged["_rmh"].to_numpy(dtype=float)
    sweep_long = (rml < prim_lvl) & (close > prim_lvl)
    sweep_short = (rmh > prim_lvl) & (close < prim_lvl)
    prim_sweep = np.where(long_mask, sweep_long, sweep_short)
    tagged["prim_sweep"] = np.where(np.isnan(prim_lvl), False, prim_sweep).astype(bool)

    return tagged.drop(columns=["_atr", "_close", "_rml", "_rmh"])


# --------------------------------------------------------------------------- #
# verdict                                                                       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CohortVerdict:
    label: str
    n_near: int
    n_far: int
    avg_near: float | None
    avg_far: float | None
    lift: float | None
    near_ci_lo: float | None
    near_ci_hi: float | None
    lift_ci_lo: float | None
    lift_ci_hi: float | None
    holm_p: float | None
    decision: str  # BUILD | NO-EDGE | INSUFFICIENT


def evaluate_primary(
    tagged: pd.DataFrame,
    *,
    min_n: int = 30,
    bar: float = 0.05,
    alpha: float = 0.05,
    n_boot: int = 10_000,
    seed: int | None = 12345,
) -> list[CohortVerdict]:
    """Pre-committed BUILD gate for the two primary cells (Holm family of 2).

    BUILD requires, on the supplied source: (1) ``n_near >= min_n``; (2) the
    near cohort significantly above ``+bar`` (``audit_guard`` block-bootstrap CI
    + Holm); (3) ``mean(near) - mean(far) > 0`` with its two-sample bootstrap CI
    excluding 0. Far control = same-direction trades with ``near_band == ">1.0"``.
    """
    specs = (("long", PRIMARY_LABELS[0]), ("short", PRIMARY_LABELS[1]))
    has_tags = not tagged.empty and "prim_sweep" in tagged.columns

    near_arrays: list[npt.NDArray[np.float64]] = []
    far_arrays: list[npt.NDArray[np.float64]] = []
    for direction, _label in specs:
        if has_tags:
            d = tagged[tagged["direction"] == direction]
            near_s = d[d["prim_sweep"] & d["prim_band"].isin(NEAR_BANDS)]["r"]
            far_s = d[d["near_band"] == ">1.0"]["r"]
            near_arrays.append(near_s.to_numpy(dtype=np.float64))
            far_arrays.append(far_s.to_numpy(dtype=np.float64))
        else:
            near_arrays.append(np.empty(0, dtype=np.float64))
            far_arrays.append(np.empty(0, dtype=np.float64))

    cells = [
        audit_guard.AuditCell(label=specs[i][1], supp_r=near_arrays[i].tolist())
        for i in range(2)
    ]
    cell_verdicts = audit_guard.evaluate_audit_cells(
        cells, bar=bar, alpha=alpha, min_n=min_n, n_boot=n_boot, seed=seed
    )

    out: list[CohortVerdict] = []
    for i, cv in enumerate(cell_verdicts):
        near, far = near_arrays[i], far_arrays[i]
        n_near, n_far = near.shape[0], far.shape[0]
        avg_far = float(np.mean(far)) if n_far else None
        lift: float | None = None
        lift_lo: float | None = None
        lift_hi: float | None = None

        if n_near < min_n:
            decision = "INSUFFICIENT"
        else:
            if n_far >= 2 and avg_far is not None:
                lift = float(np.mean(near)) - avg_far
                lift_lo, lift_hi = _two_sample_lift_ci(
                    near, far, alpha=alpha, n_boot=n_boot, seed=seed
                )
            abs_pass = cv.decision == audit_guard.DECISION_DISABLE
            lift_pass = lift_lo is not None and lift_lo > 0.0
            decision = "BUILD" if abs_pass and lift_pass else "NO-EDGE"

        out.append(
            CohortVerdict(
                label=specs[i][1],
                n_near=n_near,
                n_far=n_far,
                avg_near=cv.supp_avg,
                avg_far=avg_far,
                lift=lift,
                near_ci_lo=cv.ci_lo,
                near_ci_hi=cv.ci_hi,
                lift_ci_lo=lift_lo,
                lift_ci_hi=lift_hi,
                holm_p=cv.adj_pvalue,
                decision=decision,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# exploratory (reported, NOT gate-deciding)                                     #
# --------------------------------------------------------------------------- #

_BAND_ORDER = ["<=0.25", "<=0.5", "<=1.0", ">1.0", "n/a"]


def exploratory_summary(
    tagged: pd.DataFrame, *, src: str, min_n: int = 30
) -> list[str]:
    """Uncorrected diagnostic tables — band gradient + per-level avg_r."""
    if tagged.empty or "near_band" not in tagged.columns:
        return ["_(no rows)_"]
    lines: list[str] = []

    lines.append(f"#### Band gradient ({src})")
    lines.append("")
    lines.append("avg_r by direction × nearest-level band.")
    lines.append("")
    lines.append("| direction | band | n | avg_r |")
    lines.append("| --- | --- | ---: | ---: |")
    grad = (
        tagged.groupby(["direction", "near_band"])["r"]
        .agg(["count", "mean"])
        .reset_index()
    )
    grad["_ord"] = grad["near_band"].map({b: i for i, b in enumerate(_BAND_ORDER)})
    for _, row in grad.sort_values(["direction", "_ord"]).iterrows():
        lines.append(
            f"| {row['direction']} | {row['near_band']} | "
            f"{int(row['count'])} | {row['mean']:+.3f} |"
        )

    lines.append("")
    lines.append(f"#### Per-level near cohort ({src})")
    lines.append("")
    lines.append("avg_r for the near cohort (<=0.5 ATR) by nearest level × direction.")
    lines.append("")
    lines.append("| level | direction | n | avg_r |")
    lines.append("| --- | --- | ---: | ---: |")
    near = tagged[tagged["near_band"].isin(NEAR_BANDS)]
    per_lvl = (
        near.groupby(["near_name", "direction"])["r"]
        .agg(["count", "mean"])
        .reset_index()
    )
    for _, row in (
        per_lvl[per_lvl["count"] >= min_n]
        .sort_values("mean", ascending=False)
        .iterrows()
    ):
        lines.append(
            f"| {row['near_name']} | {row['direction']} | "
            f"{int(row['count'])} | {row['mean']:+.3f} |"
        )
    return lines


# --------------------------------------------------------------------------- #
# report                                                                        #
# --------------------------------------------------------------------------- #


def _fmt(x: float | None) -> str:
    return "—" if x is None else f"{x:+.3f}"


def _format_source_section(
    src: str, n_entries: int, verdicts: list[CohortVerdict], explor: list[str]
) -> str:
    lines = [f"## Source: `{src}`  ({n_entries} entries)", ""]
    lines.append(f"### Primary cells — {src} (pre-committed gate)")
    lines.append("")
    lines.append(
        "| cell | n_near | avg_near | near CI | n_far | avg_far | lift | lift CI | Holm p | decision |"
    )
    lines.append("| --- | ---: | ---: | --- | ---: | ---: | ---: | --- | ---: | --- |")
    for v in verdicts:
        near_ci = (
            f"[{_fmt(v.near_ci_lo)}, {_fmt(v.near_ci_hi)}]"
            if v.near_ci_lo is not None
            else "—"
        )
        lift_ci = (
            f"[{_fmt(v.lift_ci_lo)}, {_fmt(v.lift_ci_hi)}]"
            if v.lift_ci_lo is not None
            else "—"
        )
        holm = "—" if v.holm_p is None else f"{v.holm_p:.3f}"
        lines.append(
            f"| {v.label} | {v.n_near} | {_fmt(v.avg_near)} | {near_ci} | "
            f"{v.n_far} | {_fmt(v.avg_far)} | {_fmt(v.lift)} | {lift_ci} | "
            f"{holm} | **{v.decision}** |"
        )
    lines.append("")
    lines.append(f"### Exploratory — {src} (uncorrected, not gate-deciding)")
    lines.append("")
    lines.extend(explor)
    lines.append("")
    return "\n".join(lines)


def _headline_verdict(
    headline: dict[str, list[CohortVerdict]],
) -> tuple[str, str]:
    """Combine LIVE (gate) + IS into a single BUILD / PROMISING-UNPROVEN /
    NO-EDGE / INSUFFICIENT headline."""
    live = headline.get("live")
    bt = headline.get("backtest")
    if live is None:
        return ("INSUFFICIENT", "No live ledger available — IS cannot gate a build.")
    live_dec = {v.label: v.decision for v in live}
    bt_dec = {v.label: v.decision for v in (bt or [])}
    if any(d == "BUILD" for d in live_dec.values()):
        cells = [lbl for lbl, d in live_dec.items() if d == "BUILD"]
        return ("BUILD", f"Live ledger clears the gate on: {', '.join(cells)}.")
    if any(
        live_dec[lbl] == "INSUFFICIENT" and bt_dec.get(lbl) == "BUILD"
        for lbl in live_dec
    ):
        return (
            "PROMISING-UNPROVEN",
            "IS clears the gate but the live ledger is too thin to confirm OOS — "
            "do not build yet; revisit as the ledger grows.",
        )
    if any(d == "NO-EDGE" for d in live_dec.values()):
        return (
            "NO-EDGE",
            "Live near-level cohort does not clear the de-biased gate — do not build.",
        )
    return ("INSUFFICIENT", "Not enough live data in either primary cell.")


def format_report(
    headline: dict[str, list[CohortVerdict]],
    sections: list[str],
    *,
    bar: float,
    alpha: float,
    min_n: int,
    sweep_lookback: int,
) -> str:
    verdict, rationale = _headline_verdict(headline)
    out = [
        "# Reference-level proximity audit",
        "",
        "**Date:** 2026-06-24  ·  **Status:** read-only measurement (no engine change)",
        "",
        f"## Headline verdict: **{verdict}**",
        "",
        rationale,
        "",
        "Pre-committed BUILD gate (locked before running): on the **live** ledger a "
        "primary cell must clear `n >= min_n`, near-cohort bootstrap-CI lower bound "
        "`> +bar`, Holm-adjusted `p < alpha`, AND `mean(near) - mean(far) > 0` with "
        "its two-sample bootstrap CI excluding 0. Direction is split throughout. "
        "Exploratory tables are uncorrected and never gate-deciding.",
        "",
        f"Params: `bar=±{bar}R`  `alpha={alpha}`  `min_n={min_n}`  "
        f"`sweep_lookback={sweep_lookback}`. Levels: MO/WO/DO, MonH/MonL, "
        "PDH/PDL, PWH/PWL (week = Monday 00:00 UTC).",
        "",
    ]
    out.extend(sections)
    out.append("---")
    out.append("")
    out.append(
        "_Limitation: this measures whether proximity modulates **already-fired** "
        "signals — a proxy for a level-trigger's edge, since the ledger cannot "
        "contain triggers that never fired. A BUILD verdict gates a separate "
        "`reference_level` detector spec._"
    )
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# DB orchestration                                                              #
# --------------------------------------------------------------------------- #


def _load_entries(db: Path, src: str, since_ms: int | None) -> pd.DataFrame:
    with duckdb.connect(str(db), read_only=True) as conn:
        if src == "live":
            q = (
                "SELECT symbol, tf, direction, entry_price, candle_ts_ms, outcome_r "
                "FROM signal_alert_outcomes "
                "WHERE outcome_r IS NOT NULL AND entry_price IS NOT NULL "
                "AND candle_ts_ms IS NOT NULL"
            )
            if since_ms is not None:
                q += f" AND candle_ts_ms >= {since_ms}"
            return normalize_live(conn.execute(q).df())
        q = (
            "SELECT symbol, timeframe, direction, entry_price, signal_time, pnl_r "
            "FROM backtest_trades WHERE pnl_r IS NOT NULL AND entry_price IS NOT NULL"
        )
        if since_ms is not None:
            q += f" AND signal_time >= {since_ms}"
        return normalize_backtest(conn.execute(q).df())


def _load_market(
    db: Path, entries: pd.DataFrame, sweep_lookback: int
) -> tuple[dict[str, pd.DataFrame], dict[tuple[str, str], pd.DataFrame]]:
    daily_by_symbol: dict[str, pd.DataFrame] = {}
    tf_by_symbol: dict[tuple[str, str], pd.DataFrame] = {}
    day_ms = _TF_MS["1d"]
    with duckdb.connect(str(db), read_only=True) as conn:
        for symbol in entries["symbol"].unique():
            sub = entries[entries["symbol"] == symbol]
            tmin, tmax = int(sub["ts_ms"].min()), int(sub["ts_ms"].max())
            daily_by_symbol[str(symbol)] = get_ohlcv(
                conn, str(symbol), "1d", tmin - 45 * day_ms, tmax + day_ms
            )
            for tf in sub["tf"].unique():
                margin = (sweep_lookback + 2) * _tf_ms(str(tf))
                tf_by_symbol[(str(symbol), str(tf))] = get_ohlcv(
                    conn, str(symbol), str(tf), tmin - margin, tmax + _tf_ms(str(tf))
                )
    return daily_by_symbol, tf_by_symbol


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Reference-level proximity audit (read-only)"
    )
    p.add_argument("--db", type=Path, default=Path(DEFAULT_DB_PATH))
    p.add_argument("--source", choices=["live", "backtest", "both"], default="both")
    p.add_argument(
        "--days", type=int, default=None, help="only entries in the last N days"
    )
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--sweep-lookback", type=int, default=3)
    p.add_argument("--bar", type=float, default=0.05)
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--n-boot", type=int, default=10_000)
    p.add_argument("--seed", type=int, default=12345)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return p


def main() -> int:
    args = build_parser().parse_args()
    since_ms: int | None = None
    if args.days is not None:
        since_ms = (
            int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
            - args.days * _TF_MS["1d"]
        )

    srcs = ["live", "backtest"] if args.source == "both" else [args.source]
    headline: dict[str, list[CohortVerdict]] = {}
    sections: list[str] = []
    for src in srcs:
        entries = _load_entries(args.db, src, since_ms)
        if entries.empty:
            sections.append(f"## Source: `{src}`\n\n_(no entries)_\n")
            continue
        daily_by_symbol, tf_by_symbol = _load_market(
            args.db, entries, args.sweep_lookback
        )
        tagged = tag_entries(
            entries, daily_by_symbol, tf_by_symbol, sweep_lookback=args.sweep_lookback
        )
        verdicts = evaluate_primary(
            tagged,
            min_n=args.min_n,
            bar=args.bar,
            alpha=args.alpha,
            n_boot=args.n_boot,
            seed=args.seed,
        )
        headline[src] = verdicts
        sections.append(
            _format_source_section(
                src,
                len(entries),
                verdicts,
                exploratory_summary(tagged, src=src, min_n=args.min_n),
            )
        )

    report = format_report(
        headline,
        sections,
        bar=args.bar,
        alpha=args.alpha,
        min_n=args.min_n,
        sweep_lookback=args.sweep_lookback,
    )
    verdict, rationale = _headline_verdict(headline)
    print(f"Headline verdict: {verdict}\n{rationale}\n")
    for src, verdicts in headline.items():
        for v in verdicts:
            print(
                f"[{src}] {v.label}: {v.decision}  "
                f"(n_near={v.n_near} avg_near={_fmt(v.avg_near)} "
                f"lift={_fmt(v.lift)} n_far={v.n_far})"
            )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report)
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
