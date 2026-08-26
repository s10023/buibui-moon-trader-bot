"""Brief package skeleton: config defaults, completed-bar boundary, serialisation."""

from dataclasses import asdict

import pandas as pd

from analytics.brief._common import (
    DAY_MS,
    completed_bars,
    day_ahead_dow,
    day_ahead_label,
    parse_as_of_ms,
)
from analytics.brief.config import FALLBACK_SYMBOLS, BriefConfig
from analytics.brief.types import (
    BriefBundle,
    CycleState,
    HealthReport,
    PunditBoard,
    SessionRecapRow,
    SessionState,
    SessionTendencyRow,
    bundle_to_dict,
    error_panel,
)

AS_OF = 1_704_067_200_000  # 2024-01-01 00:00:00 UTC (a Monday)


def test_config_defaults() -> None:
    cfg = BriefConfig(symbols=("BTCUSDT",), as_of_ms=AS_OF)
    assert cfg.stats_days == 180
    assert cfg.zone_tfs == ("4h", "1d")
    assert cfg.max_levels_per_side == 4
    assert cfg.max_zones_per_side == 2
    assert cfg.recent_call_days == 14
    assert cfg.max_recent_calls == 10
    assert FALLBACK_SYMBOLS == ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def test_completed_bars_boundary() -> None:
    df = pd.DataFrame(
        {
            "open_time": [AS_OF - 2 * DAY_MS, AS_OF - DAY_MS, AS_OF],
            "open": [1.0, 1.0, 1.0],
            "high": [1.0, 1.0, 1.0],
            "low": [1.0, 1.0, 1.0],
            "close": [1.0, 1.0, 1.0],
        }
    )
    out = completed_bars(df, "1d", AS_OF)
    # bar opening at AS_OF - DAY_MS closes exactly at AS_OF -> completed
    assert list(out["open_time"]) == [AS_OF - 2 * DAY_MS, AS_OF - DAY_MS]


def test_parse_as_of_ms_variants() -> None:
    assert parse_as_of_ms("2024-01-01T00:00:00Z") == AS_OF
    assert parse_as_of_ms("2024-01-01T00:00:00+00:00") == AS_OF
    assert parse_as_of_ms("2024-01-01T00:00:00") == AS_OF  # naive = UTC


def test_day_ahead_helpers() -> None:
    assert day_ahead_dow(AS_OF) == "Mon"
    assert day_ahead_label(AS_OF) == "Mon 2024-01-01"


def test_bundle_to_dict_json_safe() -> None:
    bundle = BriefBundle(
        as_of_ms=AS_OF,
        day_ahead="Mon 2024-01-01",
        session_clock=None,
        # A real CycleState rather than None: `below` is a TUPLE, and this test
        # exists to prove the bundle survives json.dumps, so the new field has
        # to be present in the shape that could break it.
        cycle=CycleState(
            score=3,
            total=6,
            below=("50W SMA", "50W EMA", "200D EMA"),
            close=69_600.0,
            trigger_name="21W EMA",
            trigger_price=68_767.0,
            trigger_dist_pct=-1.1968,
            days_at_score=9,
        ),
        panels=[error_panel("BTCUSDT", "boom")],
        pundit=PunditBoard(
            priors_status="absent",
            priors_age_days=None,
            min_n_marker=None,
            ledger_status="absent",
            ledger_total=0,
            ledger_skipped=0,
            recent_calls=[],
            authors=[],
            families=[],
        ),
        health=HealthReport(rows=[], notes=[], data_ok=True),
    )
    d = bundle_to_dict(bundle)
    assert d["panels"][0]["error"] == "boom"
    assert d["pundit"]["priors_status"] == "absent"
    import json

    json.dumps(d)  # must be JSON-serialisable


def test_error_panel_sessions_none() -> None:
    assert error_panel("X", "boom").sessions is None


def test_session_state_serialises() -> None:
    state = SessionState(
        recap=[
            SessionRecapRow(
                session="Asia",
                start_ms=0,
                end_ms=6 * 3_600_000,
                date_myt="1970-01-01",
                day_offset=0,
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                net_pct=50.0,
                net_atr=0.25,
                range_atr=0.75,
                n_bars=6,
                expected_bars=6,
                made_set_high=True,
                made_set_low=False,
            )
        ],
        tendency=[SessionTendencyRow(session="Asia", high_pct=0.3, low_pct=0.4)],
    )
    d = asdict(state)
    assert d["recap"][0]["session"] == "Asia"
    assert d["tendency"][0]["high_pct"] == 0.3
