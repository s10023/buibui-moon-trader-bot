"""Brief package skeleton: config defaults, completed-bar boundary, serialisation."""

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
    HealthReport,
    PunditBoard,
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
