"""brief/sessions adapter — recap + tendency assembly and degradation."""

import pandas as pd

from analytics.brief.sessions import build_session_state
from analytics.session_windows import myt_date_str, myt_day_offset, session_at
from analytics.stats.session import SessionResult, SessionRow

H = 3_600_000
# 2024-01-01 00:00:00 UTC — Monday, exactly 08:00 MYT (Asia open).
START = 1_704_067_200_000
AS_OF = START + 30 * H  # Tuesday 14:00 MYT — Tue Asia just completed


def _h1_frame(hours: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "open_time": START + h * H,
                "open": float(h),
                "high": float(h) + 2.0,
                "low": float(h),
                "close": float(h) + 1.0,
                "volume": 1.0,
            }
            for h in hours
        ]
    )


def _tendency() -> SessionResult:
    return SessionResult(
        rows=[
            SessionRow(session="Asia", high_pct=0.31, low_pct=0.38, by_dow={}),
            SessionRow(session="London", high_pct=0.35, low_pct=0.27, by_dow={}),
            SessionRow(session="NY", high_pct=0.34, low_pct=0.35, by_dow={}),
        ]
    )


def test_full_recap_three_windows() -> None:
    state, notes = build_session_state(
        _h1_frame(list(range(30))), atr14=2.0, as_of_ms=AS_OF, tendency=_tendency()
    )
    assert notes == []
    assert state is not None and state.recap is not None
    assert [r.session for r in state.recap] == ["London", "NY", "Asia"]
    london, ny, asia = state.recap
    # London bars 6..13: open 6, close 14, high 15, low 6.
    assert london.open == 6.0 and london.close == 14.0
    assert london.high == 15.0 and london.low == 6.0
    assert london.net_atr == 4.0 and london.range_atr == 4.5
    assert london.n_bars == 8 and london.expected_bars == 8
    # Asia (Tue) bars 24..29: open 24, close 30, high 31, low 24.
    assert asia.open == 24.0 and asia.close == 30.0
    assert asia.net_pct == (30.0 - 24.0) / 24.0 * 100.0
    assert asia.n_bars == 6 and asia.expected_bars == 6
    # Set extremes: highest high = Asia (31), lowest low = London (6).
    assert asia.made_set_high and not asia.made_set_low
    assert london.made_set_low and not london.made_set_high
    assert not ny.made_set_high and not ny.made_set_low


def test_tendency_mirrored_without_by_dow() -> None:
    state, _ = build_session_state(
        _h1_frame(list(range(30))), atr14=2.0, as_of_ms=AS_OF, tendency=_tendency()
    )
    assert state is not None and state.tendency is not None
    assert [t.session for t in state.tendency] == ["Asia", "London", "NY"]
    assert state.tendency[0].high_pct == 0.31
    assert state.tendency[2].low_pct == 0.35


def test_empty_window_omitted_with_note() -> None:
    # No Tuesday-Asia bars (hours 24..29 missing).
    state, notes = build_session_state(
        _h1_frame(list(range(24))), atr14=2.0, as_of_ms=AS_OF, tendency=None
    )
    assert state is not None and state.recap is not None
    assert [r.session for r in state.recap] == ["London", "NY"]
    assert notes == ["session recap: no 1h bars for Asia window"]
    # Markers recomputed over the rows PRESENT: NY high 21 beats London 15.
    london, ny = state.recap
    assert ny.made_set_high and london.made_set_low
    assert state.tendency is None


def test_partial_window_kept_with_bar_count() -> None:
    hours = [h for h in range(30) if h not in (17, 18)]  # NY loses 2 bars
    state, notes = build_session_state(
        _h1_frame(hours), atr14=2.0, as_of_ms=AS_OF, tendency=None
    )
    assert notes == []
    assert state is not None and state.recap is not None
    ny = state.recap[1]
    assert ny.session == "NY"
    assert ny.n_bars == 4 and ny.expected_bars == 6
    assert ny.open == 14.0 and ny.close == 20.0  # OHLC from available bars


def test_all_windows_empty_recap_none() -> None:
    state, notes = build_session_state(
        _h1_frame([]), atr14=2.0, as_of_ms=AS_OF, tendency=_tendency()
    )
    assert notes == ["session recap: no 1h bars in any window"]
    assert state is not None
    assert state.recap is None and state.tendency is not None


def test_both_halves_none_collapses_state() -> None:
    state, notes = build_session_state(
        _h1_frame([]), atr14=2.0, as_of_ms=AS_OF, tendency=None
    )
    assert state is None
    assert notes == ["session recap: no 1h bars in any window"]


def _flat_bars(hours: range, high: float, low: float) -> list[dict[str, float]]:
    return [
        {
            "open_time": START + h * H,
            "open": low + 1.0,
            "high": high,
            "low": low,
            "close": high - 1.0,
            "volume": 1.0,
        }
        for h in hours
    ]


def test_set_extreme_ties_go_to_earliest_window() -> None:
    # Recap order is [London, NY, Asia]. London & Asia tie on high (50) ->
    # earliest (London) is set-high; NY & Asia tie on low (5) -> earliest of
    # the two (NY) is set-low. max/min return the first of equals.
    frame = pd.DataFrame(
        _flat_bars(range(6, 14), high=50.0, low=10.0)  # London
        + _flat_bars(range(14, 20), high=40.0, low=5.0)  # NY
        + _flat_bars(range(24, 30), high=50.0, low=5.0)  # Asia
    )
    state, _ = build_session_state(frame, atr14=2.0, as_of_ms=AS_OF, tendency=None)
    assert state is not None and state.recap is not None
    london, ny, asia = state.recap
    assert london.made_set_high and not asia.made_set_high
    assert ny.made_set_low and not asia.made_set_low


def test_zero_atr_drops_atr_fields() -> None:
    state, _ = build_session_state(
        _h1_frame(list(range(30))), atr14=0.0, as_of_ms=AS_OF, tendency=None
    )
    assert state is not None and state.recap is not None
    assert all(r.net_atr is None and r.range_atr is None for r in state.recap)
    assert state.recap[0].net_pct != 0.0  # pct never needs ATR


# --- ST94: the recap row must say WHICH DAY it names --------------------
# `session` is the row's only human-readable key, and the clock names the
# same labels for the window in progress. A consumer that does no epoch
# arithmetic -- the card model, which reads `asdict` output -- read a
# closed row as the current session and quoted yesterday's London while
# today's ran the other way. Every figure matched the panel byte for byte,
# so the FRAME was wrong, not the numbers.


def test_recap_rows_carry_the_MYT_day_they_name() -> None:
    state, _ = build_session_state(
        _h1_frame(list(range(30))), atr14=2.0, as_of_ms=AS_OF, tendency=_tendency()
    )
    assert state is not None and state.recap is not None
    got = [(r.session, r.date_myt, r.day_offset) for r in state.recap]
    assert got == [
        ("London", "2024-01-01", -1),
        ("NY", "2024-01-01", -1),
        ("Asia", "2024-01-02", 0),
    ]


def test_day_offset_is_anchored_on_the_window_START_not_its_end() -> None:
    """NY opens 22:00 MYT and closes 04:00 the NEXT MYT day.

    It is named for the day it OPENED, so start and end disagree by one
    day for that window and only that window. Anchoring on `end_ms` would
    label yesterday's NY as today's -- the exact confusion ST94 is about,
    reintroduced one field over.
    """
    state, _ = build_session_state(
        _h1_frame(list(range(30))), atr14=2.0, as_of_ms=AS_OF, tendency=_tendency()
    )
    assert state is not None and state.recap is not None
    ny = next(r for r in state.recap if r.session == "NY")
    assert ny.day_offset == -1
    assert ny.date_myt == myt_date_str(ny.start_ms) == "2024-01-01"
    # The mutation this pins: the end lands on the NEXT MYT day.
    assert myt_day_offset(ny.end_ms, AS_OF) == 0
    assert myt_date_str(ny.end_ms) == "2024-01-02"


def test_a_recap_row_sharing_the_clock_label_is_a_DIFFERENT_window() -> None:
    as_of = START + 34 * H  # Tuesday 18:00 MYT -- mid-London
    clock = session_at(as_of)
    state, _ = build_session_state(
        _h1_frame(list(range(34))), atr14=2.0, as_of_ms=as_of, tendency=_tendency()
    )
    assert state is not None and state.recap is not None
    assert clock.label == "London"
    twins = [r for r in state.recap if r.session == clock.label]
    assert twins, "expected the recap to carry a row with the clock's own label"
    # Same label, and the recap's is yesterday's while the clock's is today's.
    assert all(r.day_offset == -1 for r in twins)
    assert myt_date_str(clock.start_ms) == "2024-01-02"


def test_today_and_yesterday_are_distinguishable_without_epoch_arithmetic() -> None:
    """The whole point: `day_offset` alone separates the two."""
    state, _ = build_session_state(
        _h1_frame(list(range(30))), atr14=2.0, as_of_ms=AS_OF, tendency=_tendency()
    )
    assert state is not None and state.recap is not None
    today = {r.session for r in state.recap if r.day_offset == 0}
    prior = {r.session for r in state.recap if r.day_offset == -1}
    assert today == {"Asia"}
    assert prior == {"London", "NY"}
