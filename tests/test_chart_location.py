"""Tests for the Chart-tab location overlay (issue #822).

Three contracts: the series IS ``anchored_vwap`` (parity, so this stays
rendering rather than new math); it is CAUSAL (truncating the input never
moves an earlier point, and the profile ignores the forming hour); and it is
DISPLAY ONLY (no detector, gate, sizing or execution module imports it).
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from analytics.brief.indicators import (
    _PROFILE_DAYS,
    _month_anchor_ms,
    _profile_state,
    _week_anchor_ms,
)
from analytics.indicators import anchored_vwap
from web.api.chart_location import (
    ANCHORS_BY_TIMEFRAME,
    DISPLAY_ONLY_MARKER,
    PROFILE_DAYS,
    Anchor,
    anchor_ms,
    anchored_vwap_series,
    earliest_anchor_ms,
    profile_levels,
)

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
# 2026-01-26 00:00 UTC is a Monday; the frame below crosses a week and a month.
T0 = int(pd.Timestamp("2026-01-26T00:00:00Z").value // 1_000_000)


def _bars(n: int, step_ms: int = 4 * HOUR_MS, t0: int = T0) -> pd.DataFrame:
    rows = []
    for i in range(n):
        mid = 100.0 + 10.0 * math.sin(i * 0.3) + 0.05 * i
        rows.append(
            {
                "symbol": "BTCUSDT",
                "timeframe": "4h",
                "open_time": t0 + i * step_ms,
                "open": mid - 0.5,
                "high": mid + 2.0 + (i % 3),
                "low": mid - 2.0 - (i % 5) * 0.3,
                "close": mid + 0.5,
                "volume": 10.0 + (i % 7) * 3.0,
                "taker_buy_volume": 5.0,
            }
        )
    return pd.DataFrame(rows)


ANCHORS: tuple[Anchor, ...] = ("day", "week", "month")


class TestAnchors:
    def test_week_and_month_match_the_brief(self) -> None:
        times = pd.Series(
            [T0 + k * 7 * HOUR_MS + 123 for k in range(200)], dtype="int64"
        )
        week = anchor_ms(times, "week")
        month = anchor_ms(times, "month")
        for t, w, m in zip(times, week, month, strict=True):
            assert int(w) == _week_anchor_ms(int(t))
            assert int(m) == _month_anchor_ms(int(t))

    def test_day_is_utc_midnight(self) -> None:
        times = pd.Series([T0 + 5 * HOUR_MS, T0 + DAY_MS - 1], dtype="int64")
        assert list(anchor_ms(times, "day")) == [T0, T0]

    def test_earliest_anchor_is_the_month_start(self) -> None:
        mid_month = int(pd.Timestamp("2026-02-17T13:00:00Z").value // 1_000_000)
        assert earliest_anchor_ms(mid_month) == int(
            pd.Timestamp("2026-02-01T00:00:00Z").value // 1_000_000
        )


class TestVwapSeries:
    @pytest.mark.parametrize("anchor", ANCHORS)
    def test_every_point_equals_anchored_vwap_on_the_prefix(
        self, anchor: Anchor
    ) -> None:
        df = _bars(120)
        series = anchored_vwap_series(df, anchor)
        assert len(series) == len(df)
        anchors = anchor_ms(df["open_time"], anchor)
        for i, (t, anchored_on, value) in enumerate(series):
            assert anchored_on == int(anchors.iloc[i])
            prefix = df[df["open_time"] <= t]
            expected = anchored_vwap(prefix, int(anchors.iloc[i]))
            assert expected is not None
            assert value == pytest.approx(expected, rel=1e-12)

    @pytest.mark.parametrize("anchor", ANCHORS)
    def test_truncation_never_moves_an_earlier_point(self, anchor: Anchor) -> None:
        df = _bars(90)
        full = anchored_vwap_series(df, anchor)
        for k in (1, 7, 30, 61, 89):
            head = anchored_vwap_series(df.iloc[:k], anchor)
            assert head == full[:k]

    def test_series_resets_at_each_anchor(self) -> None:
        df = _bars(60)
        series = {p.time_ms: p.value for p in anchored_vwap_series(df, "day")}
        for t in df["open_time"]:
            if int(t) % DAY_MS == 0:  # first bar of a UTC day
                row = df[df["open_time"] == t].iloc[0]
                typical = (row["high"] + row["low"] + row["close"]) / 3.0
                assert series[int(t)] == pytest.approx(typical)

    def test_zero_volume_prefix_is_omitted_not_zero(self) -> None:
        df = _bars(6)
        df.loc[0:1, "volume"] = 0.0
        series = anchored_vwap_series(df, "month")
        assert [p.time_ms for p in series] == list(df["open_time"].iloc[2:])

    def test_empty_frame(self) -> None:
        assert anchored_vwap_series(_bars(0), "week") == []


class TestProfileLevels:
    def test_profile_days_matches_the_brief(self) -> None:
        assert PROFILE_DAYS == _PROFILE_DAYS

    def test_levels_match_the_brief_profile(self) -> None:
        hourly = _bars(24 * 70, step_ms=HOUR_MS, t0=T0 - 70 * DAY_MS)
        end_ms = int(hourly["open_time"].iloc[-1]) + HOUR_MS
        levels = profile_levels(hourly, end_ms)
        brief = _profile_state(hourly, ref_close=100.0, atr14=2.0, as_of_ms=end_ms)
        assert levels is not None and brief is not None
        assert (levels.poc, levels.vah, levels.val) == (brief.poc, brief.vah, brief.val)
        assert levels.window_start_ms >= end_ms - PROFILE_DAYS * DAY_MS

    def test_forming_hour_is_ignored(self) -> None:
        hourly = _bars(24 * 10, step_ms=HOUR_MS)
        end_ms = int(hourly["open_time"].iloc[-1]) + HOUR_MS
        closed = profile_levels(hourly, end_ms)
        forming = {
            **hourly.iloc[-1].to_dict(),
            "open_time": end_ms - HOUR_MS // 2,
            "high": 500.0,
            "low": 499.0,
            "close": 499.5,
            "volume": 1e9,
        }
        with_forming = pd.concat([hourly, pd.DataFrame([forming])], ignore_index=True)
        assert profile_levels(with_forming, end_ms) == closed

    def test_no_bars_returns_none(self) -> None:
        assert profile_levels(_bars(0), T0) is None


# ── Display only, never a gate ──────────────────────────────────────────────

_REPO = Path(__file__).resolve().parent.parent
_OVERLAY_MODULES = (
    "web.api.chart_location",
    "web.api.routers.location",
    "web.api.models.location",
)
# Every package that detects, gates, sizes or executes. The scan below covers
# the whole repo outside web/ and tests/; these are named so a rename that
# empties one cannot leave the scan passing vacuously.
_DECISION_PACKAGES = ("analytics/signal", "signals", "portfolio", "trade", "card")
_SKIP_DIRS = {"web", "tests", ".venv", "node_modules", ".git", ".claude"}


def _imports_overlay(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        else:
            continue
        if any(n.startswith(m) for n in names for m in _OVERLAY_MODULES):
            return True
    # Dynamic import by string (importlib) still names the module.
    return any(m in source for m in _OVERLAY_MODULES)


def _scanned_files() -> list[Path]:
    out = []
    for path in _REPO.rglob("*.py"):
        rel = path.relative_to(_REPO)
        if rel.parts and rel.parts[0] in _SKIP_DIRS:
            continue
        out.append(rel)
    return out


class TestDisplayOnly:
    def test_scanner_flags_an_import(self) -> None:
        # Teeth: each import spelling a consumer could use must be caught.
        assert _imports_overlay("from web.api.chart_location import anchor_ms\n")
        assert _imports_overlay("import web.api.chart_location as loc\n")
        assert _imports_overlay("from web.api import chart_location\n")
        assert _imports_overlay("from web.api.routers import location\n")
        assert _imports_overlay(
            "import importlib\nimportlib.import_module('web.api.chart_location')\n"
        )

    def test_scanner_passes_an_unrelated_import(self) -> None:
        # Specificity: the scalar primitive itself stays importable everywhere.
        assert not _imports_overlay("from analytics.indicators import anchored_vwap\n")
        assert not _imports_overlay("from web.api.routers import zones\n")

    def test_scan_covers_every_decision_package(self) -> None:
        scanned = _scanned_files()
        for pkg in _DECISION_PACKAGES:
            prefix = Path(pkg).parts
            assert any(f.parts[: len(prefix)] == prefix for f in scanned), pkg

    def test_no_detector_gate_or_sizing_path_reads_the_overlay(self) -> None:
        offenders = [
            str(rel)
            for rel in _scanned_files()
            if _imports_overlay((_REPO / rel).read_text(encoding="utf-8"))
        ]
        assert offenders == [], (
            "the Chart-tab location overlay is display only, never a gate "
            f"(issue #822); imported by {offenders}"
        )

    def test_api_carries_the_marker(self) -> None:
        assert DISPLAY_ONLY_MARKER == "display only, never a gate"


# ── API ──────────────────────────────────────────────────────────────────────


def test_location_endpoint_returns_vwap_and_profile(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, int, int]] = []
    chart = _bars(120)
    hourly = _bars(24 * 10, step_ms=HOUR_MS)

    def fake_get_ohlcv(
        _db: object, _symbol: str, timeframe: str, start: int, end: int
    ) -> pd.DataFrame:
        calls.append((timeframe, start, end))
        df = hourly if timeframe == "1h" else chart
        return df[(df["open_time"] >= start) & (df["open_time"] <= end)]

    monkeypatch.setattr("web.api.routers.location.get_ohlcv", fake_get_ohlcv)
    start_ms = T0 + 10 * DAY_MS
    end_ms = T0 + 20 * DAY_MS
    resp = web_client.get(
        f"/api/location?symbol=BTCUSDT&timeframe=4h&start_ms={start_ms}&end_ms={end_ms}"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["note"] == DISPLAY_ONLY_MARKER
    assert [s["anchor"] for s in data["vwap"]] == list(ANCHORS_BY_TIMEFRAME["4h"])
    for series in data["vwap"]:
        assert series["points"], series["anchor"]
        assert all(p["time_ms"] >= start_ms for p in series["points"])
        assert all(p["anchor_ms"] <= p["time_ms"] for p in series["points"])
    # The 4h month series opens mid-month: its first on-screen point is anchored
    # on the 1st, not on start_ms, because bars were fetched from the anchor.
    month = next(s for s in data["vwap"] if s["anchor"] == "month")
    assert month["points"][0]["anchor_ms"] == earliest_anchor_ms(start_ms)
    assert month["points"][0]["anchor_ms"] < start_ms
    # Chart bars are fetched from the month anchor so the first window is whole.
    assert calls[0] == ("4h", earliest_anchor_ms(start_ms), end_ms)
    assert calls[1][0] == "1h"
    assert data["profile"] is not None
    assert data["profile"]["val"] <= data["profile"]["poc"] <= data["profile"]["vah"]


def test_location_endpoint_on_1d_drops_the_day_anchor(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.api.routers.location.get_ohlcv", lambda *a, **kw: _bars(0))
    resp = web_client.get(
        "/api/location?symbol=BTCUSDT&timeframe=1d&start_ms=0&end_ms=9999999999999"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert [s["anchor"] for s in data["vwap"]] == ["week", "month"]
    assert data["profile"] is None
