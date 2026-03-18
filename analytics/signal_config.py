"""Pure signal watch configuration loader.

Loads TOML config from a file and provides a SignalWatchConfig dataclass.
CLI flags take precedence over config file values — the caller is responsible
for merging (see buibui.py run_signal_watch).
No module-level side effects.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


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
    cooldown_seconds: float = 3600.0
    state_file: str = "signal_state.json"
    # Per-symbol SMT secondary map: {"BTCUSDT": "ETHUSDT", ...}
    smt_pairs: dict[str, str] = field(default_factory=dict)


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

    return SignalWatchConfig(
        symbols=data.get("symbols"),
        timeframes=data.get("timeframes", ["4h"]),
        strategies=data.get("strategies"),
        telegram=bool(data.get("telegram", False)),
        min_sl_pct=float(data.get("min_sl_pct", 0.0)),
        tp_r=float(data.get("tp_r", 2.0)),
        sl_pct=float(data.get("sl_pct", 0.02)),
        cooldown_seconds=float(data.get("cooldown_seconds", 3600.0)),
        state_file=str(data.get("state_file", "signal_state.json")),
        smt_pairs=smt_pairs,
    )
