"""Research-universe config loader (N3).

The universe is the breadth set for P2 trend / P3 XS-momentum research —
deliberately separate from config/coins.json (which drives the live daemon and
backtest defaults). Selection criterion + refresh tool: tools/select_universe.py.
"""

import tomllib
from pathlib import Path

DEFAULT_UNIVERSE_PATH = Path("config/universe.toml")


def load_universe(path: Path | str = DEFAULT_UNIVERSE_PATH) -> list[str]:
    """Return the universe symbols from a [universe] TOML block.

    Symbols are stripped, uppercased, and deduped (first occurrence wins, order
    preserved). Raises FileNotFoundError if the file is missing and ValueError
    on a missing/empty/malformed symbols list.
    """
    with open(path, "rb") as f:
        data = tomllib.load(f)
    block = data.get("universe", {})
    raw = block.get("symbols", [])
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"universe config {path} has no [universe].symbols list")
    seen: set[str] = set()
    symbols: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(f"invalid symbol entry in {path}: {entry!r}")
        sym = entry.strip().upper()
        if sym not in seen:
            seen.add(sym)
            symbols.append(sym)
    return symbols
