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

    TOML example (sub-table form):
        [strategy_params.bos]
        tp_r_4h = 2.5     # 4h-specific override; other TFs use global tp_r

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
    # Note: per-strategy tp_r overrides in strategy_params are ignored in TP sweep mode
    # (you are exploring tp_r space globally). Per-strategy sl_pct overrides still apply.
    tp_r_values: list[float] = field(default_factory=list)
    # Per-strategy parameter overrides (tp_r, sl_pct, TF-specific variants).
    # Lookup order: TF-specific → strategy-wide → global tp_r / sl_pct.
    strategy_params: dict[str, StrategyOverride] = field(default_factory=dict)
    # Global ATR-based SL multiplier (None = use sl_pct instead).
    # When set, SL distance = atr_sl_multiplier × ATR14 at signal candle.
    # Per-strategy overrides in strategy_params take precedence.
    atr_sl_multiplier: float | None = None
    # When non-empty, sweep ATR multiplier values and print a comparison table.
    # e.g. [0.5, 1.0, 1.5, 2.0, 2.5] — shows avg R per strategy × TF at each multiplier.
    # Overrides atr_sl_multiplier for comparison only; tp_r and per-strategy overrides apply.
    atr_sl_multiplier_values: list[float] = field(default_factory=list)
    # liquidity_sweep entry mode:
    #   True  (default) — fib-extension mode: entry at 1.13/1.27 fib extension of range
    #   False           — pivot-sweep mode: entry on wick above pivot high + close inside
    # Set to false in TOML to compare win rates between the two approaches.
    liq_sweep_use_fib: bool = True
    # fib-mode close variant (only applies when liq_sweep_use_fib=True):
    #   False (default) — close must come back below the fib extension level (1.13/1.27)
    #   True            — close must come back below the original swing_high (inside range)
    # Stricter confirmation: wick reaches fib zone but body closes fully inside the range.
    liq_sweep_fib_range_close: bool = False
    # ADR bias gate: when set, suppress signals where today's range >= this fraction of ADR-14
    # in the chasing direction (same logic as live BiasConfig.adr_suppress_threshold).
    # Mirrors the [bias] TOML section used by the signal watcher.
    adr_suppress_threshold: float | None = None

    def effective_min_trades(self, tf: str) -> int:
        """Return per-TF override if configured, else the global min_trades."""
        return self.min_trades_per_tf.get(tf, self.min_trades)

    def effective_tp_r(self, strategy: str, symbol: str, tf: str) -> float:
        """Resolve tp_r: symbol+TF → symbol → TF-specific → strategy-wide → global."""
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
            if k.startswith("tp_r_") and k != "tp_r" and not isinstance(v, dict)
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
        strategy_params[str(strat_name)] = StrategyOverride(
            tp_r=float(tp_r_val) if tp_r_val is not None else None,
            sl_pct=float(sl_pct_val) if sl_pct_val is not None else None,
            atr_sl_multiplier=float(atr_sl_val) if atr_sl_val is not None else None,
            tp_r_per_tf=tp_r_per_tf,
            sl_pct_per_tf=sl_pct_per_tf,
            atr_sl_multiplier_per_tf=atr_sl_per_tf,
            per_symbol=per_symbol,
        )

    # Some signal_watch configs place liq_sweep_use_fib inside a [backtest]
    # sub-table; fall back to that if not present at the top level.
    _bt_section: dict[str, object] = data.get("backtest", {})

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
        strategy_params=strategy_params,
        atr_sl_multiplier_values=[
            float(v) for v in data.get("atr_sl_multiplier_values", [])
        ],
        atr_sl_multiplier=(
            float(data["atr_sl_multiplier"])
            if data.get("atr_sl_multiplier") is not None
            else None
        ),
        liq_sweep_use_fib=bool(
            data.get("liq_sweep_use_fib", _bt_section.get("liq_sweep_use_fib", True))
        ),
        liq_sweep_fib_range_close=bool(
            data.get(
                "liq_sweep_fib_range_close",
                _bt_section.get("liq_sweep_fib_range_close", False),
            )
        ),
        adr_suppress_threshold=(
            float(data["bias"]["adr_suppress_threshold"])
            if isinstance(data.get("bias"), dict)
            and data["bias"].get("adr_suppress_threshold") is not None
            else None
        ),
    )
