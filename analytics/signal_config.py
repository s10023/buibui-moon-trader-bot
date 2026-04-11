"""Pure signal watch configuration loader.

Loads TOML config from a file and provides a SignalWatchConfig dataclass.
CLI flags take precedence over config file values — the caller is responsible
for merging (see buibui.py run_signal_watch).
No module-level side effects.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base.

    Scalars and arrays: override wins.
    Dicts/tables: merged key-by-key (override wins per key, base keys not in override are kept).
    """
    result: dict[str, Any] = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _load_toml_with_extends(path: str | Path) -> dict[str, Any]:
    """Load a TOML file, merging a base file first if an 'extends' key is present.

    The 'extends' value must be a filename relative to the config file's directory.
    The base file is loaded first; the child file's keys are deep-merged on top
    (child wins on conflicts). The 'extends' key is consumed and not passed to callers.
    """
    resolved = Path(path)
    with open(resolved, "rb") as f:
        data: dict[str, Any] = tomllib.load(f)
    base_name = data.pop("extends", None)
    if base_name is not None:
        base_path = resolved.parent / str(base_name)
        with open(base_path, "rb") as f:
            base: dict[str, Any] = tomllib.load(f)
        data = _deep_merge(base, data)
    return data


@dataclass
class SymbolOverride:
    """Per-symbol parameter overrides within a strategy block.

    TOML example (sub-table under strategy_params):
        [strategy_params.doji.ETHUSDT]
        tp_r_15m = 4.5     # ETH 15m only
        tp_r_1h = 4.0      # ETH 1h only

    Lookup order within a symbol block: TF-specific → symbol-wide.
    """

    tp_r: float | None = None
    sl_pct: float | None = None
    atr_sl_multiplier: float | None = None
    tp_r_per_tf: dict[str, float] = field(default_factory=dict)
    sl_pct_per_tf: dict[str, float] = field(default_factory=dict)
    atr_sl_multiplier_per_tf: dict[str, float] = field(default_factory=dict)


@dataclass
class StrategyOverride:
    """Per-strategy parameter overrides.

    Lookup order for each param: symbol+TF → symbol → TF-specific → strategy-wide → global.

    TOML example (signal_watch.toml):
        [strategy_params.engulfing]
        tp_r = 3.0        # all TFs unless overridden below
        tp_r_4h = 3.0     # 4h-specific override
        tp_r_1h = 2.5

        [strategy_params.doji]
        tp_r = 4.0           # all symbols fallback

        [strategy_params.doji.BTCUSDT]
        tp_r_15m = 3.5

        [strategy_params.doji.ETHUSDT]
        tp_r_15m = 4.5
    """

    tp_r: float | None = None
    sl_pct: float | None = None
    atr_sl_multiplier: float | None = None
    tp_r_per_tf: dict[str, float] = field(default_factory=dict)
    sl_pct_per_tf: dict[str, float] = field(default_factory=dict)
    atr_sl_multiplier_per_tf: dict[str, float] = field(default_factory=dict)
    per_symbol: dict[str, SymbolOverride] = field(default_factory=dict)
    adr_exempt: bool = False
    # None = inherit global [backtest].volume_suppress; True/False = per-strategy override.
    volume_suppress: bool | None = None
    # None = inherit global [backtest].volume_spike_boost; True/False = per-strategy override.
    volume_spike_boost: bool | None = None
    # Optional direction-split TP multiples. Falls back to tp_r when None.
    tp_r_long: float | None = None
    tp_r_short: float | None = None


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
    # hard mode only: suppress if win_rate < this (legacy — kept for TOML back-compat)
    filter_threshold: float = 0.45
    # hard mode only: suppress if directional avg_r < this (replaces win-rate gate)
    # 0.0 = must have positive EV; set lower to allow marginally negative strategies
    min_avg_r: float = 0.0
    # Optional direction-split thresholds. When set, these override min_avg_r for that
    # direction only. Falls back to min_avg_r when None.
    min_avg_r_long: float | None = None
    min_avg_r_short: float | None = None
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
    # Exempt spike candles (volume > 3× rolling mean) from volume_suppress.
    # When True, a spike candle passes even if volume_suppress is on for that strategy.
    # Default off — enable per-strategy after confirming spike edge via volume split table.
    volume_spike_boost: bool = False

    def effective_min_trades(self, tf: str) -> int:
        """Return per-TF override if configured, else the global min_trades."""
        return self.min_trades_per_tf.get(tf, self.min_trades)


@dataclass
class BiasConfig:
    """Configuration for the statistics-driven bias layer (F8).

    Controls two gates applied after the backtest/volume filters:

    1. ADR hard suppress: drop signals when today's range has already consumed
       >= adr_suppress_threshold of the 14-day ADR (e.g. 0.80 = 80%).
       None = disabled (default).

    2. DOW soft suppress: reduce confidence by 1 star when the signal direction
       opposes today's historical DOW avg return.  Only fires when abs(avg_return)
       >= dow_suppress_min_abs_return (dead-band, default 0.5%).
    """

    adr_suppress_threshold: float | None = None
    dow_soft_suppress: bool = False
    dow_suppress_min_abs_return: float = 0.005


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
    # Per-strategy parameter overrides (tp_r, sl_pct, TF-specific variants).
    # Lookup order: TF-specific → strategy-wide → global tp_r / sl_pct.
    strategy_params: dict[str, StrategyOverride] = field(default_factory=dict)
    # Global ATR-based SL multiplier (None = use sl_pct instead).
    # When set, SL distance = atr_sl_multiplier × ATR14 at signal candle.
    # Per-strategy overrides in strategy_params take precedence.
    atr_sl_multiplier: float | None = None
    # Statistics-driven bias layer (F8): ADR progress gate + DOW soft suppress.
    bias: BiasConfig = field(default_factory=BiasConfig)

    def effective_tp_r(
        self, strategy: str, symbol: str, tf: str, direction: str = ""
    ) -> float:
        """Resolve tp_r: symbol+TF → symbol → TF-specific → directional → strategy-wide → global."""
        override = self.strategy_params.get(strategy)
        if override is not None:
            sym = override.per_symbol.get(symbol)
            if sym is not None:
                if tf in sym.tp_r_per_tf:
                    return sym.tp_r_per_tf[tf]
                if sym.tp_r is not None:
                    return sym.tp_r
            if tf in override.tp_r_per_tf:
                return override.tp_r_per_tf[tf]
            if direction == "long" and override.tp_r_long is not None:
                return override.tp_r_long
            if direction == "short" and override.tp_r_short is not None:
                return override.tp_r_short
            if override.tp_r is not None:
                return override.tp_r
        return self.tp_r

    def effective_sl_pct(self, strategy: str, symbol: str, tf: str) -> float:
        """Resolve sl_pct: symbol+TF → symbol → TF-specific → strategy-wide → global."""
        override = self.strategy_params.get(strategy)
        if override is not None:
            sym = override.per_symbol.get(symbol)
            if sym is not None:
                if tf in sym.sl_pct_per_tf:
                    return sym.sl_pct_per_tf[tf]
                if sym.sl_pct is not None:
                    return sym.sl_pct
            if tf in override.sl_pct_per_tf:
                return override.sl_pct_per_tf[tf]
            if override.sl_pct is not None:
                return override.sl_pct
        return self.sl_pct

    def effective_volume_suppress(self, strategy: str) -> bool:
        """Return per-strategy volume_suppress if set, else the global [backtest] flag."""
        override = self.strategy_params.get(strategy)
        if override is not None and override.volume_suppress is not None:
            return override.volume_suppress
        return self.backtest.volume_suppress

    def effective_volume_spike_boost(self, strategy: str) -> bool:
        """Return per-strategy volume_spike_boost if set, else the global [backtest] flag."""
        override = self.strategy_params.get(strategy)
        if override is not None and override.volume_spike_boost is not None:
            return override.volume_spike_boost
        return self.backtest.volume_spike_boost

    def effective_atr_sl_multiplier(
        self, strategy: str, symbol: str, tf: str
    ) -> float | None:
        """Resolve atr_sl_multiplier: symbol+TF → symbol → TF-specific → strategy-wide → global."""
        override = self.strategy_params.get(strategy)
        if override is not None:
            sym = override.per_symbol.get(symbol)
            if sym is not None:
                if tf in sym.atr_sl_multiplier_per_tf:
                    return sym.atr_sl_multiplier_per_tf[tf]
                if sym.atr_sl_multiplier is not None:
                    return sym.atr_sl_multiplier
            if tf in override.atr_sl_multiplier_per_tf:
                return override.atr_sl_multiplier_per_tf[tf]
            if override.atr_sl_multiplier is not None:
                return override.atr_sl_multiplier
        return self.atr_sl_multiplier


def load_signal_config(path: str | Path) -> SignalWatchConfig:
    """Load SignalWatchConfig from a TOML file.

    Raises FileNotFoundError if path does not exist.
    Raises tomllib.TOMLDecodeError if the file is not valid TOML.
    Raises ValueError if smt_pairs values are not strings.
    """
    data = _load_toml_with_extends(path)

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
        min_avg_r=float(raw_bt.get("min_avg_r", 0.0)),
        min_avg_r_long=(
            float(raw_bt["min_avg_r_long"])
            if raw_bt.get("min_avg_r_long") is not None
            else None
        ),
        min_avg_r_short=(
            float(raw_bt["min_avg_r_short"])
            if raw_bt.get("min_avg_r_short") is not None
            else None
        ),
        save_results=bool(raw_bt.get("save_results", True)),
        # [backtest].fee_pct takes precedence; falls back to top-level fee_pct
        fee_pct=float(raw_bt.get("fee_pct", data.get("fee_pct", 0.0))),
        # [backtest].min_sl_pct takes precedence; falls back to top-level min_sl_pct
        min_sl_pct=float(raw_bt.get("min_sl_pct", data.get("min_sl_pct", 0.0))),
        volume_suppress=bool(raw_bt.get("volume_suppress", False)),
        volume_spike_boost=bool(raw_bt.get("volume_spike_boost", False)),
    )

    raw_strategy_params = data.get("strategy_params", {})
    if not isinstance(raw_strategy_params, dict):
        raise ValueError("strategy_params must be a TOML table of strategy sub-tables")
    strategy_params: dict[str, StrategyOverride] = {}
    for strat_name, vals in raw_strategy_params.items():
        if not isinstance(vals, dict):
            raise ValueError(
                f"strategy_params.{strat_name} must be a TOML table "
                "(e.g. [strategy_params.engulfing] or inline {{tp_r = 3.0}})"
            )
        tp_r_per_tf = {
            k[len("tp_r_") :]: float(v)
            for k, v in vals.items()
            if k.startswith("tp_r_")
            and k not in ("tp_r", "tp_r_long", "tp_r_short")
            and not isinstance(v, dict)
        }
        sl_pct_per_tf = {
            k[len("sl_pct_") :]: float(v)
            for k, v in vals.items()
            if k.startswith("sl_pct_") and k != "sl_pct" and not isinstance(v, dict)
        }
        atr_sl_per_tf = {
            k[len("atr_sl_multiplier_") :]: float(v)
            for k, v in vals.items()
            if k.startswith("atr_sl_multiplier_")
            and k != "atr_sl_multiplier"
            and not isinstance(v, dict)
        }
        tp_r_val = vals.get("tp_r")
        sl_pct_val = vals.get("sl_pct")
        atr_sl_val = vals.get("atr_sl_multiplier")
        per_symbol: dict[str, SymbolOverride] = {}
        for sym_key, sym_vals in vals.items():
            if isinstance(sym_vals, dict):
                sym_tp_r_per_tf = {
                    k[len("tp_r_") :]: float(v)
                    for k, v in sym_vals.items()
                    if k.startswith("tp_r_") and k != "tp_r"
                }
                sym_sl_pct_per_tf = {
                    k[len("sl_pct_") :]: float(v)
                    for k, v in sym_vals.items()
                    if k.startswith("sl_pct_") and k != "sl_pct"
                }
                sym_atr_sl_per_tf = {
                    k[len("atr_sl_multiplier_") :]: float(v)
                    for k, v in sym_vals.items()
                    if k.startswith("atr_sl_multiplier_") and k != "atr_sl_multiplier"
                }
                sym_tp_r_val = sym_vals.get("tp_r")
                sym_sl_pct_val = sym_vals.get("sl_pct")
                sym_atr_sl_val = sym_vals.get("atr_sl_multiplier")
                per_symbol[sym_key] = SymbolOverride(
                    tp_r=float(sym_tp_r_val) if sym_tp_r_val is not None else None,
                    sl_pct=float(sym_sl_pct_val)
                    if sym_sl_pct_val is not None
                    else None,
                    atr_sl_multiplier=(
                        float(sym_atr_sl_val) if sym_atr_sl_val is not None else None
                    ),
                    tp_r_per_tf=sym_tp_r_per_tf,
                    sl_pct_per_tf=sym_sl_pct_per_tf,
                    atr_sl_multiplier_per_tf=sym_atr_sl_per_tf,
                )
        raw_vs = vals.get("volume_suppress")
        raw_vsb = vals.get("volume_spike_boost")
        raw_tp_r_long = vals.get("tp_r_long")
        raw_tp_r_short = vals.get("tp_r_short")
        strategy_params[str(strat_name)] = StrategyOverride(
            tp_r=float(tp_r_val) if tp_r_val is not None else None,
            sl_pct=float(sl_pct_val) if sl_pct_val is not None else None,
            atr_sl_multiplier=float(atr_sl_val) if atr_sl_val is not None else None,
            tp_r_per_tf=tp_r_per_tf,
            sl_pct_per_tf=sl_pct_per_tf,
            atr_sl_multiplier_per_tf=atr_sl_per_tf,
            per_symbol=per_symbol,
            adr_exempt=bool(vals.get("adr_exempt", False)),
            volume_suppress=bool(raw_vs) if raw_vs is not None else None,
            volume_spike_boost=bool(raw_vsb) if raw_vsb is not None else None,
            tp_r_long=float(raw_tp_r_long) if raw_tp_r_long is not None else None,
            tp_r_short=float(raw_tp_r_short) if raw_tp_r_short is not None else None,
        )

    raw_bias = data.get("bias", {})
    raw_adr = raw_bias.get("adr_suppress_threshold")
    bias = BiasConfig(
        adr_suppress_threshold=float(raw_adr) if raw_adr is not None else None,
        dow_soft_suppress=bool(raw_bias.get("dow_soft_suppress", False)),
        dow_suppress_min_abs_return=float(
            raw_bias.get("dow_suppress_min_abs_return", 0.005)
        ),
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
        strategy_params=strategy_params,
        atr_sl_multiplier=(
            float(data["atr_sl_multiplier"])
            if data.get("atr_sl_multiplier") is not None
            else None
        ),
        bias=bias,
    )
