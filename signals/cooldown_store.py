"""Candle watermark dedup store for signal alerts.

Tracks the last alerted candle open_time per (symbol, timeframe, strategy,
UTC weekday of the candle). Prevents re-alerting on the same candle across
multiple scan cycles.

State is persisted to a JSON file so dedup survives daemon restarts.

WHY THE KEY CARRIES A WEEKDAY (SoT N8, fixed 2026-08-07)
-------------------------------------------------------
The three signal-watch configs partition the week with ZERO overlap —
`{Mon,Fri}` / `{Tue,Wed,Thu}` / `{Sat,Sun}` — and the picker chooses by TODAY's
UTC weekday while `day_filter` gates on each CANDLE's weekday. A candle is
therefore only scannable on a day whose config admits its weekday.

With a flat `symbol:timeframe:strategy` key the watermark is one strictly
monotonic number per key. Miss Monday, and Tuesday's own fires drag it past
Monday; by Friday — the next day whose config admits Monday — the Monday candle
sits below the watermark and `is_new_candle` refuses it forever. It was logged
only at `debug`.

Days self-heal iff the NEXT day runs the SAME config (Tue→Wed, Wed→Thu,
Sat→Sun). Mon, Thu, Fri and Sun each END a config block, so 4 of 7 days were
permanently lossy. The loss bit only keys that fired in between, which means it
preferentially deleted the HIGHEST-ACTIVITY cells — day-of-week SHAPED bias on
an axis that is itself a live conditioning gate, i.e. a skewed sample rather
than merely a thinner one.

Scoping by weekday makes each weekday's watermark advance only on its own
candles, so Friday's replay of Monday is no longer blocked by Tuesday's fire.

**Scoping by CONFIG would not have worked** — Mon and Fri share the `mon_fri`
filter, so Friday's fire would still advance a shared Mon/Fri watermark past
Monday. The scope has to be the weekday.
"""

import datetime as dt
import json
from contextlib import suppress
from pathlib import Path

_LEGACY_KEY_PARTS = 3


def _weekday(open_time: int) -> int:
    """UTC weekday (Mon=0) of a candle's open_time in epoch-ms.

    UTC, not local, so the scope matches the `day_filter` gate — which reads
    each candle's UTC `open_time` — rather than drifting by the operator's
    offset.
    """
    return dt.datetime.fromtimestamp(open_time / 1000, tz=dt.UTC).weekday()


def _key(symbol: str, timeframe: str, strategy: str, open_time: int) -> str:
    return f"{symbol}:{timeframe}:{strategy}:{_weekday(open_time)}"


class CooldownStore:
    def __init__(self, state_file: str) -> None:
        self._path = Path(state_file)
        self._watermarks: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        # Corrupted or unreadable state file: start empty rather than crash the daemon.
        with suppress(json.JSONDecodeError, OSError):
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._watermarks = self._migrate(data.get("watermarks", {}))

    @staticmethod
    def _migrate(raw: dict[str, int]) -> dict[str, int]:
        """Seed every weekday scope from a legacy 3-part key.

        Seeding rather than discarding is deliberate. A dropped watermark leaves
        the key cold, and the cold-start guard in `scanner.py` then restricts it
        to the latest candle only — so discarding would trade one permanent bug
        for a week of degraded catch-up across every key, for no gain. Seeding
        makes day-one behaviour identical to pre-fix and lets the scopes diverge
        naturally from there.

        Idempotent: an already-migrated file has only 4-part keys and passes
        through untouched, so this is safe to run on every load.
        """
        out: dict[str, int] = {}
        for key, ts in raw.items():
            if key.count(":") == _LEGACY_KEY_PARTS - 1:
                for wd in range(7):
                    out[f"{key}:{wd}"] = ts
            else:
                out[key] = ts
        return out

    def _save(self) -> None:
        self._path.write_text(
            json.dumps({"watermarks": self._watermarks}, indent=2), encoding="utf-8"
        )

    def is_new_candle(
        self, symbol: str, timeframe: str, strategy: str, open_time: int
    ) -> bool:
        """Return True if open_time is newer than the last alerted candle.

        Comparison is within this candle's own weekday scope, so an un-replayed
        Monday is not suppressed by a Tuesday that fired after it.
        """
        return (
            self._watermarks.get(_key(symbol, timeframe, strategy, open_time), -1)
            < open_time
        )

    def last_marked(
        self, symbol: str, timeframe: str, strategy: str, open_time: int
    ) -> int | None:
        """Last alerted candle open_time for this key's weekday scope, or None.

        Backs the catch-up cold-start guard: `is_new_candle` answers True both
        for a genuinely missed candle and for a key that has never fired, so it
        cannot distinguish "replay from the watermark" from "first contact,
        take the latest candle only".

        Takes `open_time` so the guard resolves the SAME scope it protects —
        without that a newly created weekday scope would look warm and replay
        its whole window on first contact.
        """
        return self._watermarks.get(_key(symbol, timeframe, strategy, open_time))

    def mark_candle(
        self, symbol: str, timeframe: str, strategy: str, open_time: int
    ) -> None:
        """Record open_time as the last alerted candle and persist."""
        self._watermarks[_key(symbol, timeframe, strategy, open_time)] = open_time
        self._save()
