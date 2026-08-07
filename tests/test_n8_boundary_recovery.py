"""SoT N8 — the catch-up x weekday-config boundary evidence loss.

THE DEFECT. The three signal-watch configs partition the week with zero overlap
(`{Mon,Fri}` / `{Tue,Wed,Thu}` / `{Sat,Sun}`), and the picker chooses by TODAY's
UTC weekday while `day_filter` gates on each CANDLE's weekday. So a candle can
only be scanned on a day whose config admits its weekday.

The watermark was keyed `symbol:timeframe:strategy` — no day scope — and is
strictly monotonic. Miss Monday, and Tuesday's own fires drag that single shared
watermark past Monday; by Friday (the next day whose config admits Monday) the
Monday candle sits below the watermark and `is_new_candle` refuses it. It is
never revisited, logged only at `debug`.

Days self-heal iff the NEXT day runs the SAME config: Tue->Wed, Wed->Thu,
Sat->Sun. The other four — Mon, Thu, Fri, Sun — each end a config block and are
permanently lost. The loss bites only keys that fired in between, so it
preferentially deletes the highest-activity cells, making the damage
day-of-week SHAPED rather than merely thinner. That is bias on an axis that is
itself a live conditioning gate.

Measured 2026-08-07 before the fix: 10 of 49 boundary days in 85 had zero fires.
"""

import datetime as dt
import json

from analytics.signal._common import scan_window
from signals.cooldown_store import CooldownStore

_TF = "1h"
_STRAT = "fvg"
_SYM = "BTCUSDT"


def _ms(day: str, hour: int = 12) -> int:
    """UTC epoch-ms for a 2026-08 date at a given hour."""
    d = dt.datetime.fromisoformat(f"{day}T{hour:02d}:00:00+00:00")
    return int(d.timestamp() * 1000)


# 2026-08-03 is a Monday.
_MON = _ms("2026-08-03")
_TUE = _ms("2026-08-04")
_WED = _ms("2026-08-05")
_THU = _ms("2026-08-06")
_FRI = _ms("2026-08-07")


class TestBoundaryRecovery:
    def test_monday_survives_a_week_of_intervening_fires(
        self, tmp_path: object
    ) -> None:
        """THE REGRESSION. Monday must still be replayable on Friday.

        Reproduces the exact sequence: Monday missed, Tue/Wed/Thu fire, Friday's
        config finally admits Monday. Pre-fix this returned False because the one
        shared watermark had advanced to Thursday.
        """
        store = CooldownStore(str(tmp_path / "s.json"))  # type: ignore[operator]

        for ts in (_TUE, _WED, _THU):
            store.mark_candle(_SYM, _TF, _STRAT, ts)

        assert store.is_new_candle(_SYM, _TF, _STRAT, _MON), (
            "Monday was dragged below the watermark by later-weekday fires — "
            "this is the N8 permanent loss"
        )

    def test_same_weekday_still_dedups(self, tmp_path: object) -> None:
        """NEGATIVE CONTROL — without this, simply disabling dedup would pass.

        Scoping must not weaken same-weekday dedup, or every scan cycle
        re-alerts the candle it just alerted.
        """
        store = CooldownStore(str(tmp_path / "s.json"))  # type: ignore[operator]
        store.mark_candle(_SYM, _TF, _STRAT, _MON)

        assert not store.is_new_candle(_SYM, _TF, _STRAT, _MON), (
            "same candle re-alerted"
        )
        assert not store.is_new_candle(_SYM, _TF, _STRAT, _MON - 3_600_000), (
            "an OLDER same-weekday candle re-alerted"
        )
        assert store.is_new_candle(_SYM, _TF, _STRAT, _MON + 3_600_000), (
            "a NEWER same-weekday candle was wrongly suppressed"
        )

    def test_scopes_are_independent_per_weekday(self, tmp_path: object) -> None:
        store = CooldownStore(str(tmp_path / "s.json"))  # type: ignore[operator]
        store.mark_candle(_SYM, _TF, _STRAT, _MON)
        for other in (_TUE, _WED, _THU, _FRI):
            assert store.is_new_candle(_SYM, _TF, _STRAT, other)

    def test_cold_start_guard_still_distinguishes_unseen_scopes(
        self, tmp_path: object
    ) -> None:
        """`last_marked` must stay scope-aware or the cold-start guard breaks.

        The guard restricts a never-fired key to the latest candle only, so that
        a fresh state file does not burst the whole window into the ledger. It
        keys off the SAME key it protects, so scoping it is what keeps a newly
        created weekday scope from replaying 200 bars on first contact.
        """
        store = CooldownStore(str(tmp_path / "s.json"))  # type: ignore[operator]
        assert store.last_marked(_SYM, _TF, _STRAT, _MON) is None
        store.mark_candle(_SYM, _TF, _STRAT, _MON)
        assert store.last_marked(_SYM, _TF, _STRAT, _MON) == _MON
        assert store.last_marked(_SYM, _TF, _STRAT, _TUE) is None

    def test_legacy_flat_state_is_migrated_not_discarded(
        self, tmp_path: object
    ) -> None:
        """A pre-fix state file must seed every weekday scope.

        Dropping it instead would leave all 176 keys cold, and the cold-start
        guard would then restrict each to the latest candle — a week of degraded
        catch-up for no gain. Seeding keeps day-one behaviour identical.
        """
        p = tmp_path / "s.json"  # type: ignore[operator]
        p.write_text(json.dumps({"watermarks": {f"{_SYM}:{_TF}:{_STRAT}": _THU}}))

        store = CooldownStore(str(p))
        for ts in (_MON, _TUE, _WED, _THU):
            assert not store.is_new_candle(_SYM, _TF, _STRAT, ts), (
                "legacy watermark was dropped — old candles would replay"
            )
        assert store.is_new_candle(_SYM, _TF, _STRAT, _THU + 3_600_000)


class TestScanWindowReach:
    """The watermark fix is INERT unless the candle is still in the scan window.

    Recovery gaps run 3 days (Fri->Mon) to 6 days (Sun->next Sat). At a flat 200
    bars, 15m reaches only 2.1 days — shorter than even the shortest gap — so no
    15m boundary candle could ever be replayed. 15m is 64.4% of the live ledger,
    so the watermark change alone would have fixed 35.6% of the affected volume
    while looking like a complete fix.
    """

    def test_15m_window_spans_the_worst_recovery_gap(self) -> None:
        bars = scan_window("15m")
        assert bars * 15 / 60 / 24 >= 6.0, (
            f"15m window of {bars} bars spans {bars * 15 / 60 / 24:.1f}d, "
            "less than the 6-day Sun->Sat gap: N8 recovery is inert on 15m"
        )

    def test_other_timeframes_already_reach_and_are_not_widened(self) -> None:
        """NEGATIVE CONTROL — do not pay for width nothing needs.

        1h/4h/1d already span >=8.3 days at 200 bars. Widening them would triple
        the data loaded per cycle for zero recovery benefit.
        """
        for tf, mins in (("1h", 60), ("4h", 240), ("1d", 1440)):
            bars = scan_window(tf)
            assert bars == 200, f"{tf} was widened unnecessarily to {bars}"
            assert bars * mins / 60 / 24 >= 6.0
