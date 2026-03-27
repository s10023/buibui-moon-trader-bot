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
    min_sl_pct: float = 0.0
    # Global fallback — use effective_min_trades(tf) to get per-TF value.
    # Sweep mode applies this to total closed trades (not directional) for table filtering.
    min_trades: int = 20
    # Per-TF override: check this first, fall back to min_trades.
    # Calibrated from backtest_runs DB (total closed trades, 200d window): 15m→30, 1h→20, 4h→10, 1d→5
    # Note: signal_config.py uses directional trade counts (lower values) for Telegram alerts.
    # Signal rate is NOT uniform — higher TFs fire less frequently per candle.
    # To scale for a different lookback: new_value = base × (your_days / 200)
    min_trades_per_tf: dict[str, int] = field(default_factory=dict)
    # Per-symbol SMT secondary map: {"BTCUSDT": "ETHUSDT", ...}
    smt_pairs: dict[str, str] = field(default_factory=dict)
    # Suppress signals by day: "off" | "weekdays" (Mon–Fri) | "tue_thu" (Tue–Thu only)
    day_filter: str = "off"
    # EMA-50 trend gate for smt_divergence (1=on, 0=off)
    smt_trend_filter: int = 1
    # Persist aggregate results to backtest_runs table in DB
    save_results: bool = False
    # When non-empty, run the full sweep once per value and print a TP ratio comparison
    # table showing avg R per strategy at each tp_r. e.g. [1.0, 1.5, 2.0, 2.5, 3.0]
    # Overrides the single tp_r value for the purpose of comparison only.
    tp_r_values: list[float] = field(default_factory=list)

    def effective_min_trades(self, tf: str) -> int:
        """Return per-TF override if configured, else the global min_trades."""
        return self.min_trades_per_tf.get(tf, self.min_trades)


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

    per_tf = {
        k[len("min_trades_") :]: int(v)
        for k, v in data.items()
        if k.startswith("min_trades_") and k != "min_trades"
    }
    return BacktestSweepConfig(
        symbols=data.get("symbols"),
        timeframes=data.get("timeframes", ["4h"]),
        strategies=data.get("strategies"),
        days=int(data.get("days", 90)),
        sl_pct=float(data.get("sl_pct", 0.02)),
        tp_r=float(data.get("tp_r", 2.0)),
        fee_pct=float(data.get("fee_pct", 0.0)),
        min_sl_pct=float(data.get("min_sl_pct", 0.0)),
        min_trades=int(data.get("min_trades", 20)),
        min_trades_per_tf=per_tf,
        smt_pairs=smt_pairs,
        day_filter=str(data.get("day_filter", "off")),
        smt_trend_filter=int(data.get("smt_trend_filter", 1)),
        save_results=bool(data.get("save_results", False)),
        tp_r_values=[float(v) for v in data.get("tp_r_values", [])],
    )
