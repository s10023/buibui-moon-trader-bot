"""Pure backtest sweep configuration loader.

Loads TOML config from a file and provides a BacktestSweepConfig dataclass.
CLI flags take precedence over config values — the caller is responsible
for merging (see buibui.py run_backtest).
No module-level side effects.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BacktestSweepConfig:
    """All configurable options for a backtest sweep."""

    symbols: list[str] | None = None  # None = all from coins.json
    timeframes: list[str] = field(default_factory=lambda: ["4h"])
    strategies: list[str] | None = None  # None = all non-seasonality strategies
    days: int = 90
    sl_pct: float = 0.02
    tp_r: float = 2.0
    fee_pct: float = 0.0
    min_trades: int = 20
    # Per-symbol SMT secondary map: {"BTCUSDT": "ETHUSDT", ...}
    smt_pairs: dict[str, str] = field(default_factory=dict)
    # Suppress Monday and Friday signals (ICT weekly cycle)
    day_filter: bool = False


def load_backtest_config(path: str | Path) -> BacktestSweepConfig:
    """Load BacktestSweepConfig from a TOML file.

    Raises FileNotFoundError if path does not exist.
    Raises tomllib.TOMLDecodeError if the file is not valid TOML.
    Raises ValueError if smt_pairs values are not strings.
    """
    with open(path, "rb") as f:
        data = tomllib.load(f)

    raw_smt = data.get("smt_pairs", {})
    if not isinstance(raw_smt, dict):
        raise ValueError(
            "smt_pairs must be a TOML table of PRIMARY = 'SECONDARY' entries"
        )
    smt_pairs: dict[str, str] = {str(k): str(v) for k, v in raw_smt.items()}

    return BacktestSweepConfig(
        symbols=data.get("symbols"),
        timeframes=data.get("timeframes", ["4h"]),
        strategies=data.get("strategies"),
        days=int(data.get("days", 90)),
        sl_pct=float(data.get("sl_pct", 0.02)),
        tp_r=float(data.get("tp_r", 2.0)),
        fee_pct=float(data.get("fee_pct", 0.0)),
        min_trades=int(data.get("min_trades", 20)),
        smt_pairs=smt_pairs,
        day_filter=bool(data.get("day_filter", False)),
    )
