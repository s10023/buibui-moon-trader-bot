"""Pure signal watch configuration loader.

Loads TOML config from a file and provides a SignalWatchConfig dataclass.
CLI flags take precedence over config file values — the caller is responsible
for merging (see buibui.py run_signal_watch).
No module-level side effects.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def _day_filter_to_weekdays(day_filter: str) -> list[int] | None:
    """Convert day_filter mode string to allowed weekday list (Mon=0…Sun=6).

    Modes:
      "off"      — no filter (all days)
      "weekdays" — Mon–Fri (removes Sat, Sun)
      "no_monfi" — remove Mon + Fri only; weekends still pass (legacy behaviour)
      "tue_thu"  — Tue–Thu only (removes Mon, Fri, Sat, Sun)
    """
    if day_filter == "weekdays":
        return [0, 1, 2, 3, 4]  # Mon–Fri
    if day_filter == "no_monfi":
        return [1, 2, 3, 5, 6]  # Tue, Wed, Thu, Sat, Sun
    if day_filter == "tue_thu":
        return [1, 2, 3]  # Tue–Thu only
    return None  # "off" — no filter


@dataclass
class BacktestFilterConfig:
    """Configuration for the per-signal backtest filter."""

    # "soft": always fire alert, append win rate line
    # "hard": suppress alert if win_rate < filter_threshold (and enough trades)
    # "off":  disable entirely
    mode: str = "soft"
    days: int = 90
    # Below this trade count the filter is bypassed (win rate is noise).
    # Global fallback — use effective_min_trades(tf) to get per-TF value.
    min_trades: int = 12
    # Per-TF override: check this first, fall back to min_trades.
    # Calibrated from backtest_runs DB (directional trade counts, not total):
    #   15m→20, 1h→12, 4h→5, 1d→2
    # Thresholds apply to the directional bucket (long or short), not total closed trades.
    # DB p25 directional: 15m=58, 1h=17, 4h=4-5, 1d=0-1 — values set to cover ~75%+ of runs.
    # Signal rate is NOT uniform — higher TFs fire less frequently per candle.
    # To scale for a different lookback: new_value = base × (your_days / 200)
    min_trades_per_tf: dict[str, int] = field(default_factory=dict)
    # hard mode only: suppress if win_rate < this
    filter_threshold: float = 0.45
    # Persist computed backtest results to backtest_runs table (default on)
    save_results: bool = True
    # Taker fee per leg (e.g. 0.0005 = 0.05%); applied to each backtest trade
    fee_pct: float = 0.0
    # Minimum SL distance from entry as a fraction (e.g. 0.005 = 0.5%).
    # Widens structural SLs that land too close to entry (prevents fee-drag explosion).
    min_sl_pct: float = 0.0
    # Suppress alerts when the signal candle has volume < 1.5× its 20-candle rolling mean.
    # Enable after confirming via `make buibui-backtest` volume split that low-vol trades
    # underperform. Default off — investigate first.
    volume_suppress: bool = False

    def effective_min_trades(self, tf: str) -> int:
        """Return per-TF override if configured, else the global min_trades."""
        return self.min_trades_per_tf.get(tf, self.min_trades)


@dataclass
class SignalWatchConfig:
    """All configurable options for the signal watch daemon."""

    symbols: list[str] | None = None
    timeframes: list[str] = field(default_factory=lambda: ["4h"])
    strategies: list[str] | None = None
    telegram: bool = False
    min_sl_pct: float = 0.0
    tp_r: float = 2.0
    sl_pct: float = 0.02
    state_file: str = "signal_state.json"
    # Per-symbol SMT secondary map: {"BTCUSDT": "ETHUSDT", ...}
    smt_pairs: dict[str, str] = field(default_factory=dict)
    backtest: BacktestFilterConfig = field(default_factory=BacktestFilterConfig)
    # Suppress signals by day: "off" | "weekdays" (Mon–Fri) | "tue_thu" (Tue–Thu only)
    day_filter: str = "off"
    # EMA-50 trend gate for smt_divergence (1=on, 0=off)
    smt_trend_filter: int = 1
    # Per-strategy timeframe allow-list: {"trend_day": ["4h", "1d"], ...}
    # Strategies not listed here run on all configured timeframes.
    strategy_timeframes: dict[str, list[str]] = field(default_factory=dict)


def load_signal_config(path: str | Path) -> SignalWatchConfig:
    """Load SignalWatchConfig from a TOML file.

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

    raw_stf = data.get("strategy_timeframes", {})
    if not isinstance(raw_stf, dict):
        raise ValueError(
            "strategy_timeframes must be a TOML table of strategy = [timeframes] entries"
        )
    strategy_timeframes: dict[str, list[str]] = {
        str(k): [str(tf) for tf in v] for k, v in raw_stf.items()
    }

    raw_bt = data.get("backtest", {})
    bt_per_tf = {
        k[len("min_trades_") :]: int(v)
        for k, v in raw_bt.items()
        if k.startswith("min_trades_") and k != "min_trades"
    }
    backtest = BacktestFilterConfig(
        mode=str(raw_bt.get("mode", "soft")),
        days=int(raw_bt.get("days", 90)),
        min_trades=int(raw_bt.get("min_trades", 20)),
        min_trades_per_tf=bt_per_tf,
        filter_threshold=float(raw_bt.get("filter_threshold", 0.45)),
        save_results=bool(raw_bt.get("save_results", True)),
        # [backtest].fee_pct takes precedence; falls back to top-level fee_pct
        fee_pct=float(raw_bt.get("fee_pct", data.get("fee_pct", 0.0))),
        # [backtest].min_sl_pct takes precedence; falls back to top-level min_sl_pct
        min_sl_pct=float(raw_bt.get("min_sl_pct", data.get("min_sl_pct", 0.0))),
        volume_suppress=bool(raw_bt.get("volume_suppress", False)),
    )

    return SignalWatchConfig(
        symbols=data.get("symbols"),
        timeframes=data.get("timeframes", ["4h"]),
        strategies=data.get("strategies"),
        telegram=bool(data.get("telegram", False)),
        min_sl_pct=float(data.get("min_sl_pct", 0.0)),
        tp_r=float(data.get("tp_r", 2.0)),
        sl_pct=float(data.get("sl_pct", 0.02)),
        state_file=str(data.get("state_file", "signal_state.json")),
        smt_pairs=smt_pairs,
        backtest=backtest,
        day_filter=str(data.get("day_filter", "off")),
        smt_trend_filter=int(data.get("smt_trend_filter", 1)),
        strategy_timeframes=strategy_timeframes,
    )
