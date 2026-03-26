"""Candle watermark dedup store for signal alerts.

Tracks the last alerted candle open_time per (symbol, timeframe, strategy).
Prevents re-alerting on the same candle across multiple scan cycles.

State is persisted to a JSON file so dedup survives daemon restarts.
"""

import json
from pathlib import Path


class CooldownStore:
    def __init__(self, state_file: str) -> None:
        self._path = Path(state_file)
        self._watermarks: dict[str, int] = {}  # "symbol:tf:strategy" → open_time ms
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                self._watermarks = data.get("watermarks", {})
            except (json.JSONDecodeError, OSError):
                pass

    def _save(self) -> None:
        self._path.write_text(json.dumps({"watermarks": self._watermarks}, indent=2))

    def is_new_candle(
        self, symbol: str, timeframe: str, strategy: str, open_time: int
    ) -> bool:
        """Return True if open_time is newer than the last alerted candle."""
        key = f"{symbol}:{timeframe}:{strategy}"
        return self._watermarks.get(key, -1) < open_time

    def mark_candle(
        self, symbol: str, timeframe: str, strategy: str, open_time: int
    ) -> None:
        """Record open_time as the last alerted candle and persist."""
        key = f"{symbol}:{timeframe}:{strategy}"
        self._watermarks[key] = open_time
        self._save()
