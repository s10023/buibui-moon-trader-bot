"""Tests for `tools/wick_anchor_study.py` — the ST56 driver's report path.

The driver's full run is ~30 minutes over 337k fires, so the report path was
previously only ever exercised by that run. These build a small synthetic paired
set instead, which is what lets a signature change be caught in CI rather than
half an hour into a study.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType

import pandas as pd


def _load() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "tools" / "wick_anchor_study.py"
    spec = importlib.util.spec_from_file_location("wick_anchor_study", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wick_anchor_study"] = mod
    spec.loader.exec_module(mod)
    return mod


study = _load()


def _pairs(n_per_cell: int = 40) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    day0 = date(2026, 1, 1)
    for si, symbol in enumerate(("BTCUSDT", "ETHUSDT")):
        for direction in ("long", "short"):
            for i in range(n_per_cell):
                prod_r = 2.0 if i % 3 == 0 else -1.0
                wick_r = 2.0 if i % 4 == 0 else -1.0
                rows.append(
                    {
                        "symbol": symbol,
                        "timeframe": "1h",
                        "direction": direction,
                        "day": day0 + timedelta(days=i + si),
                        "geometry": "floor" if i % 2 else "structural",
                        "tp_r": 2.0,
                        "prod_sl_pct": 0.5,
                        "wick_sl_pct": 0.3,
                        "prod_r": prod_r,
                        "wick_r": wick_r,
                        "diff_r": wick_r - prod_r,
                        "ambiguous": i % 5 == 0,
                        "prod_ambiguous": False,
                        "wick_ambiguous": i % 5 == 0,
                        "diff_r_optimistic": (wick_r - prod_r)
                        + (2.0 if i % 5 == 0 else 0.0),
                    }
                )
    return pd.DataFrame(rows)


class TestReport:
    def test_reports_the_gate_and_the_sensitivity(self) -> None:
        out = "\n".join(study.report(_pairs(), study.Drops(), fires=200, n_boot=50))
        assert "THE GATE" in out
        assert "SENSITIVITY" in out
        assert "VERDICT" in out

    def test_the_sensitivity_is_labelled_as_a_bound_not_a_verdict(self) -> None:
        """The disclosure is load-bearing: a post-hoc bound is not a verdict."""
        out = "\n".join(study.report(_pairs(), study.Drops(), fires=200, n_boot=50))
        assert "not the verdict" in out

    def test_geometry_split_names_the_floor(self) -> None:
        """`floor` is the mechanism ST56 never named; it must stay visible."""
        out = "\n".join(study.report(_pairs(), study.Drops(), fires=200, n_boot=50))
        assert "floor" in out
        assert "structural" in out

    def test_drop_rate_is_reported_against_the_reversal_threshold(self) -> None:
        drops = study.Drops(no_legal_entry=1, wick_wrong_side=80, unresolved=2)
        out = "\n".join(study.report(_pairs(), drops, fires=200, n_boot=50))
        assert "40%" in out
        assert "40.0%" in out, (
            "80 of 200 fires must read as the drop rate, not be hidden"
        )

    def test_empty_pairs_degrades_rather_than_raising(self) -> None:
        empty = pd.DataFrame(columns=list(_pairs().columns))
        out = "\n".join(study.report(empty, study.Drops(), fires=0, n_boot=50))
        assert "NO PAIRED TRADES" in out


class TestDrops:
    def test_total_sums_every_reason(self) -> None:
        assert study.Drops(no_legal_entry=1, wick_wrong_side=2, unresolved=3).total == 6
