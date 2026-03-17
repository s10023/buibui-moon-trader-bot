"""Two-layer dedup store for signal alerts.

Layer 1 — candle watermark: tracks last alerted open_time per (symbol, timeframe, strategy).
Layer 2 — cooldown timer: tracks earliest next-alert time per (symbol, strategy, direction).

State is persisted to a JSON file so dedup survives daemon restarts.
"""

import json
import time
from pathlib import Path


class CooldownStore:
    def __init__(self, state_file: str) -> None:
        self._path = Path(state_file)
        self._watermarks: dict[str, int] = {}  # "symbol:tf:strategy" → open_time ms
        self._cooldowns: dict[str, float] = {}  # "symbol:strategy:direction" → epoch s
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                self._watermarks = data.get("watermarks", {})
                self._cooldowns = data.get("cooldowns", {})
            except (json.JSONDecodeError, OSError):
                pass

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(
                {"watermarks": self._watermarks, "cooldowns": self._cooldowns},
                indent=2,
            )
        )

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

    def is_off_cooldown(self, symbol: str, strategy: str, direction: str) -> bool:
        """Return True if the cooldown period has elapsed."""
        key = f"{symbol}:{strategy}:{direction}"
        return time.time() >= self._cooldowns.get(key, 0.0)

    def set_cooldown(
        self, symbol: str, strategy: str, direction: str, seconds: float
    ) -> None:
        """Set a cooldown expiring `seconds` from now and persist."""
        key = f"{symbol}:{strategy}:{direction}"
        self._cooldowns[key] = time.time() + seconds
        self._save()
