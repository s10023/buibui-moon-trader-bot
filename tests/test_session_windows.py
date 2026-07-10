"""session_windows — MYT session partition calendar math."""

from analytics.session_windows import (
    CurrentSession,
    SessionWindow,
    last_completed_windows,
    session_at,
)

H = 3_600_000
DAY = 86_400_000
# 2024-01-01 00:00:00 UTC — Monday, exactly 08:00 MYT (Asia open).
START = 1_704_067_200_000


def test_asia_open_boundary() -> None:
    cur = session_at(START)
    assert cur == CurrentSession(
        label="Asia",
        start_ms=START,
        end_ms=START + 6 * H,
        is_overlap=False,
        next_label="London",
        next_start_ms=START + 6 * H,
    )


def test_asia_last_millisecond() -> None:
    assert session_at(START + 6 * H - 1).label == "Asia"


def test_london_open_boundary() -> None:
    cur = session_at(START + 6 * H)
    assert cur.label == "London"
    assert cur.start_ms == START + 6 * H
    assert cur.end_ms == START + 14 * H
    assert cur.is_overlap is False
    assert cur.next_label == "NY"
    assert cur.next_start_ms == START + 14 * H


def test_london_ny_overlap_flag() -> None:
    # 19:59 MYT -> no overlap; 20:00 and 21:59 MYT -> overlap (still London).
    assert session_at(START + 12 * H - 1).is_overlap is False
    at_2000 = session_at(START + 12 * H)
    assert at_2000.label == "London" and at_2000.is_overlap is True
    at_2159 = session_at(START + 14 * H - 1)
    assert at_2159.label == "London" and at_2159.is_overlap is True


def test_ny_crosses_midnight() -> None:
    # 22:00 MYT Monday and 03:00 MYT Tuesday are the SAME NY window.
    at_open = session_at(START + 14 * H)
    at_0300 = session_at(START + 19 * H)
    assert at_open.label == at_0300.label == "NY"
    assert at_open.start_ms == at_0300.start_ms == START + 14 * H
    assert at_open.end_ms == at_0300.end_ms == START + 20 * H
    # Next Asia = window end + the 4h Off gap.
    assert at_open.next_label == "Asia"
    assert at_open.next_start_ms == START + 24 * H


def test_off_gap() -> None:
    cur = session_at(START + 20 * H)  # 04:00 MYT Tuesday
    assert cur.label == "Off"
    assert cur.start_ms == START + 20 * H
    assert cur.end_ms == START + 24 * H
    assert cur.is_overlap is False
    assert cur.next_label == "Asia"
    assert cur.next_start_ms == START + 24 * H
    assert session_at(START + 24 * H - 1).label == "Off"


def test_last_completed_at_asia_open() -> None:
    # At Monday 08:00 MYT the 3 completed sessions are ALL of Sunday's.
    wins = last_completed_windows(START, n=3)
    assert wins == [
        SessionWindow("Asia", START - 24 * H, START - 18 * H),
        SessionWindow("London", START - 18 * H, START - 10 * H),
        SessionWindow("NY", START - 10 * H, START - 4 * H),
    ]


def test_last_completed_mid_london() -> None:
    # Tuesday 14:00 MYT: Tue Asia just completed (end == as_of counts),
    # then Mon London + Mon NY before it.
    wins = last_completed_windows(START + 30 * H, n=3)
    assert wins == [
        SessionWindow("London", START + 6 * H, START + 14 * H),
        SessionWindow("NY", START + 14 * H, START + 20 * H),
        SessionWindow("Asia", START + 24 * H, START + 30 * H),
    ]


def test_last_completed_during_off() -> None:
    # Tuesday 06:00 MYT (Off): the full prior Monday cycle.
    wins = last_completed_windows(START + 22 * H, n=3)
    assert [w.label for w in wins] == ["Asia", "London", "NY"]
    assert wins[0].start_ms == START
    assert wins[2].end_ms == START + 20 * H


def test_last_completed_n_larger() -> None:
    # Ascending completed at Tue 14:00 MYT: ... Sun NY, Mon Asia,
    # Mon London, Mon NY, Tue Asia — the last five:
    wins = last_completed_windows(START + 30 * H, n=5)
    assert len(wins) == 5
    assert [w.label for w in wins] == ["NY", "Asia", "London", "NY", "Asia"]
    assert all(w.end_ms <= START + 30 * H for w in wins)
    assert wins == sorted(wins, key=lambda w: w.start_ms)
