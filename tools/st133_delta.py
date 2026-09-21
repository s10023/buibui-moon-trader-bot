#!/usr/bin/env python3
"""ST133 - a DIRECTION-AWARE delta of live ``tp_r`` against the sweep commit gate.

Every live ``tp_r`` was picked on highest OOS ``avg_r`` alone, weeks before the
commit gate existed (last ``tp_r`` commits #342 2026-05-03 / #364 2026-05-13; the
gate landed #422 2026-06-06). ST133 is the decision about what to do with the
cells whose current value would not survive today's filter.

**Why this tool exists.** The 2026-09-20 hand-built delta answered that question
with ONE number per cell, resolved with no direction. Production resolves
``symbol+TF -> symbol -> TF-specific -> directional -> strategy-wide -> global``
(:func:`analytics.signal.resolvers._resolve_tp_r`), and a direction-less call
SKIPS the directional branch entirely. ``pin_bar`` and ``morning_evening_star``
both declare ``tp_r_long`` / ``tp_r_short`` in the inherited base - and they are
roughly two-thirds of live alert exposure.

Measured on that artifact: all nine summary figures reproduce exactly, and the
``fallback`` column is still wrong on **35 of 175 cells carrying 63.6% of
exposure**, because removal there lands on the directional value (pin_bar long
5.0, mes long 4.0) rather than the 2.0 the table reported. The long side moves
UP where the table said targets would roughly halve.

That is a FRAME error, not an arithmetic one, which is why review missed it: the
direction-less value is genuinely what ``scanner.py:1233`` stores into
``backtest_runs``, so the table faithfully describes what the GATE sees. The
decision is about what LIVE ALERTS do, and ``atr_floor.py:97`` passes
``event.direction``. Same number for 140 cells, different for 35, and the 35 are
the big ones.

So this tool resolves every cell twice - once per direction - and reports the
direction-less value beside it so the two frames can never be silently conflated
again. It reads sweep-derived fields (gate verdict, OOS ``avg_r``, alert rate)
from a verdicts file and recomputes every CONFIG-derived field itself, so the
config half needs no DB and is reproducible on a clean clone.

This tool CHANGES NOTHING. Writing ``config/signal_watch*.toml`` is the
deployment - the 15-minute timer runs the working tree - and that stays an
operator decision.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# A bare `python3 tools/st133_delta.py` puts `tools/` on sys.path rather than the
# repo root, so `analytics.*` would not import. Per ST101 the GUARANTEE is
# tests/test_st133_delta.py::test_bare_invocation_works, never this line.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO_ROOT))

from analytics.signal.resolvers import _resolve_tp_r  # noqa: E402
from analytics.signal_config import (  # noqa: E402
    SignalWatchConfig,
    StrategyOverride,
    declared_cells,
    load_signal_config,
)

CONFIG_BY_DAY_FILTER: dict[str, str] = {
    "tue_thu": "config/signal_watch.toml",
    "mon_fri": "config/signal_watch_weekdays.toml",
    "weekend": "config/signal_watch_all.toml",
}

DIRECTIONS: tuple[str, ...] = ("long", "short")

DEFAULT_VERDICTS = "docs/plans/scratch/st133-delta-2026-09-20.json"


@dataclass(frozen=True)
class Supplier:
    """The config key that supplied a resolved ``tp_r``, as an editable identity.

    ``level`` names the branch; ``tf`` / ``symbol`` / ``direction`` carry the
    specifics, so two cells share a key exactly when their tuples are equal.
    That is what makes the collateral count in :func:`supplier_scope` mean
    "cells that move if you delete this one line".
    """

    level: str
    tf: str | None = None
    symbol: str | None = None
    direction: str | None = None

    @property
    def label(self) -> str:
        """Human-readable form for the rendered table."""
        if self.level == "tf":
            return f"TF ({self.tf})"
        if self.level == "symbol+tf":
            return f"symbol+TF ({self.symbol}/{self.tf})"
        if self.level == "symbol":
            return f"symbol ({self.symbol})"
        if self.level == "directional":
            return f"directional ({self.direction})"
        return self.level


def resolve_with_level(
    override: StrategyOverride | None,
    symbol: str,
    tf: str,
    global_tp_r: float,
    direction: str,
) -> tuple[float, Supplier]:
    """Resolve ``tp_r`` AND name the key that supplied it.

    This restates the branch order of
    :func:`analytics.signal.resolvers._resolve_tp_r`, which this repo treats as a
    defect by default. It is here because the decision needs the *identity* of
    the supplying key in order to say what deleting a TOML line would do, and the
    production resolver returns only the value. The restatement is pinned by
    ``test_resolve_with_level_matches_production_resolver``, which asserts the two
    agree on every declared cell of every live config in both directions - so a
    drift fails a test rather than silently mis-describing an edit.
    """
    if override is None:
        return global_tp_r, Supplier("global")
    sym = override.per_symbol.get(symbol)
    if sym is not None:
        if tf in sym.tp_r_per_tf:
            return sym.tp_r_per_tf[tf], Supplier("symbol+tf", tf=tf, symbol=symbol)
        if sym.tp_r is not None:
            return sym.tp_r, Supplier("symbol", symbol=symbol)
    if tf in override.tp_r_per_tf:
        return override.tp_r_per_tf[tf], Supplier("tf", tf=tf)
    if direction == "long" and override.tp_r_long is not None:
        return override.tp_r_long, Supplier("directional", direction="long")
    if direction == "short" and override.tp_r_short is not None:
        return override.tp_r_short, Supplier("directional", direction="short")
    if override.tp_r is not None:
        return override.tp_r, Supplier("strategy-wide")
    return global_tp_r, Supplier("global")


def strip_supplier(override: StrategyOverride, sup: Supplier) -> StrategyOverride:
    """A copy of ``override`` with the single key named by ``sup`` removed.

    This models the ONE TOML line an operator would delete. Anything below it in
    the resolution order then supplies the cell, which is the whole question
    ST133 asks - and the reason the answer is per direction.
    """
    out = copy.deepcopy(override)
    if sup.level == "symbol+tf" and sup.symbol is not None and sup.tf is not None:
        so = out.per_symbol.get(sup.symbol)
        if so is not None:
            so.tp_r_per_tf.pop(sup.tf, None)
    elif sup.level == "symbol" and sup.symbol is not None:
        so = out.per_symbol.get(sup.symbol)
        if so is not None:
            so.tp_r = None
    elif sup.level == "tf" and sup.tf is not None:
        out.tp_r_per_tf.pop(sup.tf, None)
    elif sup.level == "directional":
        if sup.direction == "long":
            out.tp_r_long = None
        else:
            out.tp_r_short = None
    elif sup.level == "strategy-wide":
        out.tp_r = None
    return out


def config_symbols(cfg: SignalWatchConfig, coins_path: Path | None = None) -> list[str]:
    """Symbols this config watches: its own list, else ``config/coins.json``.

    ``coins.json`` is gitignored, so callers that must run on a clean clone (the
    tests) pass an explicit ``cfg.symbols`` or ``coins_path`` instead of relying
    on it being present.
    """
    if cfg.symbols:
        return list(cfg.symbols)
    path = coins_path or (REPO_ROOT / "config" / "coins.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data)


def supplier_scope(
    cfg: SignalWatchConfig,
    symbols: list[str],
    strategy: str,
    sup: Supplier,
    direction: str,
) -> int:
    """How many declared cells resolve through the SAME key, in this direction.

    A count above 1 means the edit is not expressible per cell: deleting that
    line moves every cell in the count. ``declared_cells`` is direction-aware, so
    the universe itself can differ between long and short.
    """
    params = cfg.strategy_params or {}
    override = params.get(strategy)
    n = 0
    for strat, tf in declared_cells(cfg, direction):
        if strat != strategy:
            continue
        for sym in symbols:
            _, got = resolve_with_level(override, sym, tf, cfg.tp_r, direction)
            if got == sup:
                n += 1
    return n


@dataclass(frozen=True)
class DeltaRow:
    """One (cell x direction) row: what it is now, and what removal would do."""

    day_filter: str
    strategy: str
    tf: str
    symbol: str
    direction: str
    gate: str
    oos_avg_r: float | None
    dsr: float | None
    alerts_wk: float
    now: float
    level: str
    removed: float
    removed_level: str
    scope: int
    # The old artifact's frame, kept beside the real one so the two can never be
    # silently conflated again.
    direction_less_now: float
    direction_less_removed: float
    misdescribed: bool


def build_rows(
    verdicts: list[dict[str, object]],
    cfgs: dict[str, SignalWatchConfig],
    symbols_by_df: dict[str, list[str]],
) -> list[DeltaRow]:
    """Join sweep-derived verdicts to config-derived values, once per direction."""
    rows: list[DeltaRow] = []
    for v in verdicts:
        df = str(v["day_filter"])
        strategy, tf, symbol = str(v["strategy"]), str(v["tf"]), str(v["symbol"])
        cfg = cfgs[df]
        params = cfg.strategy_params or {}
        override = params.get(strategy)
        dl_now = _resolve_tp_r(params, strategy, symbol, tf, cfg.tp_r)
        raw_oos = v.get("oos_avg_r")
        raw_dsr = v.get("dsr")
        raw_alerts = v.get("alerts_wk")
        for direction in DIRECTIONS:
            now, sup = resolve_with_level(override, symbol, tf, cfg.tp_r, direction)
            if override is None:
                removed, rsup = now, sup
                dl_removed = dl_now
            else:
                stripped = strip_supplier(override, sup)
                removed, rsup = resolve_with_level(
                    stripped, symbol, tf, cfg.tp_r, direction
                )
                dl_params = dict(params)
                dl_params[strategy] = stripped
                dl_removed = _resolve_tp_r(dl_params, strategy, symbol, tf, cfg.tp_r)
            rows.append(
                DeltaRow(
                    day_filter=df,
                    strategy=strategy,
                    tf=tf,
                    symbol=symbol,
                    direction=direction,
                    gate=str(v.get("gate", "")),
                    oos_avg_r=float(raw_oos)
                    if isinstance(raw_oos, (int, float))
                    else None,
                    dsr=float(raw_dsr) if isinstance(raw_dsr, (int, float)) else None,
                    alerts_wk=(
                        float(raw_alerts)
                        if isinstance(raw_alerts, (int, float))
                        else 0.0
                    ),
                    now=now,
                    level=sup.label,
                    removed=removed,
                    removed_level=rsup.label,
                    scope=supplier_scope(
                        cfg, symbols_by_df[df], strategy, sup, direction
                    ),
                    direction_less_now=dl_now,
                    direction_less_removed=dl_removed,
                    misdescribed=(removed != dl_removed or now != dl_now),
                )
            )
    return rows


def _exposure(rows: list[DeltaRow]) -> float:
    """Sum ``alerts_wk`` ONCE per cell.

    It is a per-cell rate, and every cell appears twice in ``rows`` (long and
    short), so summing the rows directly double-counts the whole book.
    """
    seen: set[tuple[str, str, str, str]] = set()
    total = 0.0
    for r in rows:
        key = (r.day_filter, r.strategy, r.tf, r.symbol)
        if key not in seen:
            seen.add(key)
            total += r.alerts_wk
    return total


def render_markdown(rows: list[DeltaRow]) -> str:
    """Summary plus the cells the direction-less table misdescribes."""
    cells = {(r.day_filter, r.strategy, r.tf, r.symbol) for r in rows}
    bad = [r for r in rows if r.misdescribed]
    bad_cells = {(r.day_filter, r.strategy, r.tf, r.symbol) for r in bad}
    total_expo = _exposure(rows)
    bad_expo = _exposure(bad)
    pct = 100 * bad_expo / total_expo if total_expo else 0.0

    out: list[str] = []
    out.append("# ST133 - direction-aware tp_r delta\n")
    out.append(f"- cells: **{len(cells)}** ({len(rows)} cell x direction rows)")
    out.append(f"- total live exposure: **{total_expo:.1f} alerts/wk**")
    out.append(
        f"- cells the direction-less table MISDESCRIBES: **{len(bad_cells)}** "
        f"carrying **{bad_expo:.1f} alerts/wk** ({pct:.1f}% of exposure)"
    )
    shared = [r for r in rows if r.scope > 1]
    out.append(
        f"- rows whose supplying key is SHARED with other cells "
        f"(not editable per cell): **{len(shared)}**\n"
    )

    out.append("## Cells the direction-less table misdescribes\n")
    out.append(
        "| day_filter | strategy | tf | symbol | dir | gate | now | "
        "removed | dir-less removed | supplied by | scope | alerts/wk |"
    )
    out.append("|---|---|---|---|---|---|---:|---:|---:|---|---:|---:|")
    for r in sorted(bad, key=lambda x: (-x.alerts_wk, x.strategy, x.direction)):
        out.append(
            f"| {r.day_filter} | {r.strategy} | {r.tf} | {r.symbol} | "
            f"{r.direction} | {r.gate} | {r.now:.1f} | **{r.removed:.1f}** | "
            f"{r.direction_less_removed:.1f} | {r.level} | {r.scope} | "
            f"{r.alerts_wk:.1f} |"
        )
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 on success, 2 on an unreadable verdicts file."""
    ap = argparse.ArgumentParser(
        prog="st133_delta.py",
        description=(
            "ST133 - direction-aware tp_r delta. Recomputes every config-derived "
            "field from the live TOMLs and carries sweep-derived verdicts "
            "through unchanged. Changes no config."
        ),
    )
    ap.add_argument("--verdicts", default=DEFAULT_VERDICTS)
    ap.add_argument("--out-json", default=None)
    ap.add_argument("--out-md", default=None)
    args = ap.parse_args(argv)

    try:
        verdicts = json.loads(Path(args.verdicts).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"!! cannot read --verdicts {args.verdicts}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(verdicts, list):
        print(f"!! --verdicts {args.verdicts} is not a JSON list", file=sys.stderr)
        return 2

    cfgs = {
        df: load_signal_config(REPO_ROOT / p) for df, p in CONFIG_BY_DAY_FILTER.items()
    }
    symbols = {df: config_symbols(c) for df, c in cfgs.items()}
    rows = build_rows(verdicts, cfgs, symbols)

    md = render_markdown(rows)
    if args.out_md:
        Path(args.out_md).write_text(md, encoding="utf-8")
    else:
        print(md)
    if args.out_json:
        Path(args.out_json).write_text(
            json.dumps([asdict(r) for r in rows], indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
