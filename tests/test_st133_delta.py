"""Tests for `tools/st133_delta.py` - the direction-aware ST133 delta.

The tool restates the branch order of
`analytics.signal.resolvers._resolve_tp_r` in order to name the key that
supplied each value, which the production resolver does not return. This repo
treats a restatement as a defect unless something pins it, so
`test_resolve_with_level_matches_production_resolver` is that pin: it asks both
implementations the same question about every declared cell of every live config
in both directions.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analytics.signal.resolvers import _resolve_tp_r  # noqa: E402
from analytics.signal_config import (  # noqa: E402
    StrategyOverride,
    SymbolOverride,
    declared_cells,
    load_signal_config,
)
from tools.st133_delta import (  # noqa: E402
    CONFIG_BY_DAY_FILTER,
    DIRECTIONS,
    DeltaRow,
    Supplier,
    _exposure,
    build_rows,
    main,
    render_markdown,
    resolve_with_level,
    strip_supplier,
    supplier_scope,
)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


class TestResolverDelegation:
    """The restated branch order must not drift from production's."""

    def test_resolve_with_level_matches_production_resolver(self) -> None:
        """Same value as `_resolve_tp_r` on every declared cell, both directions.

        This is the guarantee that licenses the restatement. A new branch in the
        production resolver, or a reordering of an existing one, fails here.
        """
        checked = 0
        for day_filter, path in CONFIG_BY_DAY_FILTER.items():
            cfg = load_signal_config(REPO / path)
            params = cfg.strategy_params or {}
            for direction in DIRECTIONS:
                for strategy, tf in declared_cells(cfg, direction):
                    for symbol in SYMBOLS:
                        want = _resolve_tp_r(
                            params, strategy, symbol, tf, cfg.tp_r, direction
                        )
                        got, _ = resolve_with_level(
                            params.get(strategy), symbol, tf, cfg.tp_r, direction
                        )
                        assert got == want, (
                            f"{day_filter}/{strategy}/{tf}/{symbol}/{direction}: "
                            f"tool {got} != production {want}"
                        )
                        checked += 1
        assert checked > 200, f"only {checked} cells checked - universe looks wrong"


class TestResolutionOrder:
    """The directional layer is the one the old delta skipped."""

    def test_directional_wins_over_strategy_wide(self) -> None:
        """A long with `tp_r_long` set must NOT fall through to strategy-wide.

        Mutation guard: drop the directional branch and long reads 3.0, which is
        exactly the frame error this tool exists to correct.
        """
        ov = StrategyOverride(tp_r=3.0, tp_r_long=5.0)
        got, sup = resolve_with_level(ov, "ETHUSDT", "15m", 2.0, "long")
        assert got == 5.0
        assert sup == Supplier("directional", direction="long")

    def test_absent_directional_falls_to_strategy_wide(self) -> None:
        """Specificity: a short with no `tp_r_short` still reaches strategy-wide."""
        ov = StrategyOverride(tp_r=3.0, tp_r_long=5.0)
        got, sup = resolve_with_level(ov, "ETHUSDT", "15m", 2.0, "short")
        assert got == 3.0
        assert sup == Supplier("strategy-wide")

    def test_tf_key_wins_over_directional(self) -> None:
        """TF-specific sits ABOVE directional, so a TF key masks `tp_r_long`."""
        ov = StrategyOverride(tp_r=3.0, tp_r_long=5.0, tp_r_per_tf={"15m": 4.0})
        got, sup = resolve_with_level(ov, "ETHUSDT", "15m", 2.0, "long")
        assert got == 4.0
        assert sup == Supplier("tf", tf="15m")

    def test_symbol_tf_wins_over_everything(self) -> None:
        ov = StrategyOverride(
            tp_r=3.0,
            tp_r_long=5.0,
            tp_r_per_tf={"15m": 4.0},
            per_symbol={"ETHUSDT": SymbolOverride(tp_r_per_tf={"15m": 1.5})},
        )
        got, sup = resolve_with_level(ov, "ETHUSDT", "15m", 2.0, "long")
        assert got == 1.5
        assert sup == Supplier("symbol+tf", tf="15m", symbol="ETHUSDT")

    def test_empty_override_falls_to_global(self) -> None:
        got, sup = resolve_with_level(None, "ETHUSDT", "15m", 2.0, "long")
        assert got == 2.0
        assert sup == Supplier("global")


class TestStripSupplier:
    """Removal must take out ONE key - the line an operator would delete."""

    def test_strip_tf_key_exposes_directional(self) -> None:
        """The ST133 case: deleting `tp_r_15m` lands on directional, not 2.0."""
        ov = StrategyOverride(tp_r=3.0, tp_r_long=5.0, tp_r_per_tf={"15m": 4.0})
        _, sup = resolve_with_level(ov, "ETHUSDT", "15m", 2.0, "long")
        after, after_sup = resolve_with_level(
            strip_supplier(ov, sup), "ETHUSDT", "15m", 2.0, "long"
        )
        assert after == 5.0
        assert after_sup == Supplier("directional", direction="long")

    def test_strip_directional_exposes_strategy_wide(self) -> None:
        ov = StrategyOverride(tp_r=3.0, tp_r_long=5.0)
        _, sup = resolve_with_level(ov, "ETHUSDT", "15m", 2.0, "long")
        after, _ = resolve_with_level(
            strip_supplier(ov, sup), "ETHUSDT", "15m", 2.0, "long"
        )
        assert after == 3.0

    def test_strip_strategy_wide_exposes_global(self) -> None:
        ov = StrategyOverride(tp_r=3.0)
        _, sup = resolve_with_level(ov, "ETHUSDT", "15m", 2.0, "short")
        after, _ = resolve_with_level(
            strip_supplier(ov, sup), "ETHUSDT", "15m", 2.0, "short"
        )
        assert after == 2.0

    def test_strip_symbol_tf_exposes_tf(self) -> None:
        ov = StrategyOverride(
            tp_r_per_tf={"15m": 4.0},
            per_symbol={"ETHUSDT": SymbolOverride(tp_r_per_tf={"15m": 1.5})},
        )
        _, sup = resolve_with_level(ov, "ETHUSDT", "15m", 2.0, "long")
        after, _ = resolve_with_level(
            strip_supplier(ov, sup), "ETHUSDT", "15m", 2.0, "long"
        )
        assert after == 4.0

    def test_strip_does_not_mutate_the_caller_s_override(self) -> None:
        """A shallow copy here would corrupt every later cell in the same run."""
        ov = StrategyOverride(tp_r=3.0, tp_r_per_tf={"15m": 4.0})
        _, sup = resolve_with_level(ov, "ETHUSDT", "15m", 2.0, "long")
        strip_supplier(ov, sup)
        assert ov.tp_r_per_tf == {"15m": 4.0}


class TestSupplierScope:
    """A shared key means the edit is not expressible per cell."""

    def test_tf_key_scope_counts_every_symbol(self) -> None:
        cfg = load_signal_config(REPO / CONFIG_BY_DAY_FILTER["tue_thu"])
        params = cfg.strategy_params or {}
        ov = params.get("doji")
        assert ov is not None, "doji override missing from the tue_thu config"
        _, sup = resolve_with_level(ov, "SOLUSDT", "15m", cfg.tp_r, "long")
        n = supplier_scope(cfg, SYMBOLS, "doji", sup, "long")
        assert n >= 1
        # A per-symbol key can only ever supply its own symbol.
        if sup.level == "symbol+tf":
            assert n == 1


class TestExposure:
    """`alerts_wk` is a per-CELL rate and every cell appears twice in rows."""

    def test_exposure_counts_each_cell_once(self) -> None:
        rows = [_row(direction=d, alerts_wk=10.0) for d in ("long", "short")]
        assert _exposure(rows) == 10.0

    def test_exposure_sums_distinct_cells(self) -> None:
        rows = [
            _row(direction="long", alerts_wk=10.0, tf="15m"),
            _row(direction="short", alerts_wk=10.0, tf="15m"),
            _row(direction="long", alerts_wk=4.0, tf="1h"),
            _row(direction="short", alerts_wk=4.0, tf="1h"),
        ]
        assert _exposure(rows) == 14.0


class TestBuildRows:
    """End-to-end on the real configs, with a synthetic verdict record."""

    def test_pin_bar_15m_is_flagged_misdescribed(self) -> None:
        """The live case: tue_thu pin_bar/15m long is 5.0, not the filed 3.0."""
        cfgs = {
            df: load_signal_config(REPO / p) for df, p in CONFIG_BY_DAY_FILTER.items()
        }
        verdict = {
            "day_filter": "tue_thu",
            "strategy": "pin_bar",
            "tf": "15m",
            "symbol": "ETHUSDT",
            "gate": "DO_NOT_COMMIT",
            "oos_avg_r": -0.1172,
            "dsr": 0.081,
            "alerts_wk": 16.5,
        }
        rows = build_rows([verdict], cfgs, dict.fromkeys(cfgs, SYMBOLS))
        assert len(rows) == 2
        longs = [r for r in rows if r.direction == "long"][0]
        assert longs.now == 5.0, "long side resolves through the base's tp_r_long"
        assert longs.direction_less_now == 3.0, "the old frame reported 3.0"
        assert longs.misdescribed is True

    def test_a_cell_with_no_directional_key_is_not_flagged(self) -> None:
        """Specificity: most cells agree across frames and must NOT be flagged."""
        cfgs = {
            df: load_signal_config(REPO / p) for df, p in CONFIG_BY_DAY_FILTER.items()
        }
        verdict = {
            "day_filter": "tue_thu",
            "strategy": "bos",
            "tf": "15m",
            "symbol": "BTCUSDT",
            "gate": "INSUFFICIENT",
            "oos_avg_r": -0.1064,
            "dsr": None,
            "alerts_wk": 2.7,
        }
        rows = build_rows([verdict], cfgs, dict.fromkeys(cfgs, SYMBOLS))
        assert all(r.misdescribed is False for r in rows)

    def test_missing_numeric_fields_do_not_crash(self) -> None:
        """A verdicts file without `dsr` / `oos_avg_r` still renders."""
        cfgs = {
            df: load_signal_config(REPO / p) for df, p in CONFIG_BY_DAY_FILTER.items()
        }
        verdict: dict[str, object] = {
            "day_filter": "weekend",
            "strategy": "bos",
            "tf": "1h",
            "symbol": "ETHUSDT",
        }
        rows = build_rows([verdict], cfgs, dict.fromkeys(cfgs, SYMBOLS))
        assert rows[0].dsr is None
        assert rows[0].oos_avg_r is None
        assert rows[0].alerts_wk == 0.0
        assert "ST133" in render_markdown(rows)


class TestCli:
    def test_rejects_a_non_list_verdicts_file(self, tmp_path: Path) -> None:
        bad = tmp_path / "v.json"
        bad.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        assert main(["--verdicts", str(bad)]) == 2

    def test_rejects_a_missing_verdicts_file(self, tmp_path: Path) -> None:
        assert main(["--verdicts", str(tmp_path / "nope.json")]) == 2

    def test_writes_both_outputs(self, tmp_path: Path) -> None:
        src = tmp_path / "v.json"
        src.write_text(
            json.dumps(
                [
                    {
                        "day_filter": "tue_thu",
                        "strategy": "pin_bar",
                        "tf": "15m",
                        "symbol": "ETHUSDT",
                        "gate": "DO_NOT_COMMIT",
                        "alerts_wk": 16.5,
                    }
                ]
            ),
            encoding="utf-8",
        )
        out_md, out_json = tmp_path / "o.md", tmp_path / "o.json"
        rc = main(
            [
                "--verdicts",
                str(src),
                "--out-md",
                str(out_md),
                "--out-json",
                str(out_json),
            ]
        )
        assert rc == 0
        assert "ST133" in out_md.read_text(encoding="utf-8")
        assert len(json.loads(out_json.read_text(encoding="utf-8"))) == 2


def test_bare_invocation_works() -> None:
    """`python3 tools/st133_delta.py` must run without PYTHONPATH=.

    It imports `analytics.*`, so a bare run puts `tools/` on sys.path and dies on
    ModuleNotFoundError without the bootstrap. Per ST101 the guarantee is THIS
    test, not the bootstrap line.
    """
    proc = subprocess.run(
        [sys.executable, "tools/st133_delta.py", "--help"],
        cwd=REPO,
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "ST133" in proc.stdout


def _row(
    *,
    direction: str,
    alerts_wk: float,
    tf: str = "15m",
) -> DeltaRow:
    """A minimal DeltaRow for the exposure arithmetic."""
    return DeltaRow(
        day_filter="tue_thu",
        strategy="pin_bar",
        tf=tf,
        symbol="ETHUSDT",
        direction=direction,
        gate="DO_NOT_COMMIT",
        oos_avg_r=None,
        dsr=None,
        alerts_wk=alerts_wk,
        now=3.0,
        level="strategy-wide",
        removed=2.0,
        removed_level="global",
        scope=1,
        direction_less_now=3.0,
        direction_less_removed=2.0,
        misdescribed=False,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
