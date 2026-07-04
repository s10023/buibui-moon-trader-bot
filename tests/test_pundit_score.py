"""Tests for tools/pundit_score.py — pundit-ledger scorer."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tools.pundit_score import load_ledger, load_overrides, parse_level_field


class TestParseLevelField:
    def test_numeric_with_commas_and_dollar(self) -> None:
        p = parse_level_field("$61,696.80")
        assert p.numbers == (61696.80,)
        assert p.zones == ()
        assert not p.unspecified

    def test_k_suffix_expansion(self) -> None:
        assert parse_level_field("81k").numbers == (81000.0,)
        assert parse_level_field("60.5k intraweek value-area low").numbers == (60500.0,)

    def test_zone_hyphen_endash_to(self) -> None:
        for text in ("57,900-58,200", "57,900–58,200", "57,900 to 58,200"):
            p = parse_level_field(text)
            assert p.zones == ((57900.0, 58200.0),), text

    def test_zone_reversed_bounds_are_sorted(self) -> None:
        assert parse_level_field("60,700-59,500").zones == ((59500.0, 60700.0),)

    def test_unspecified_markers(self) -> None:
        for text in (None, "", "unspecified", "n/a", "None"):
            assert parse_level_field(text).unspecified, repr(text)

    def test_messy_multi_number_keeps_zone_and_singles(self) -> None:
        p = parse_level_field(
            "Sweep range low ~57,900-58,200 then reclaim (price 58,254)"
        )
        assert (57900.0, 58200.0) in p.zones
        assert 58254.0 in p.numbers


class TestLoaders:
    def _good_line(self) -> dict[str, str]:
        return {
            "source": "x",
            "author": "A",
            "url": "https://x.com/A/status/1",
            "call_ts_utc": "2026-06-20T10:00:00Z",
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry": "58,000",
            "stop": "57,000",
            "target": "60,000",
            "horizon": "swing",
            "confidence": "",
            "raw_quote": "sweep and reclaim",
        }

    def test_load_ledger_parses_and_warns(self, tmp_path: Path) -> None:
        p = tmp_path / "calls.jsonl"
        p.write_text(json.dumps(self._good_line()) + "\n{not json\n", encoding="utf-8")
        calls, warnings = load_ledger(p)
        assert len(calls) == 1
        assert calls[0].author == "A"
        assert calls[0].line_no == 1
        expected_ms = int(datetime(2026, 6, 20, 10, tzinfo=UTC).timestamp() * 1000)
        assert calls[0].call_ts_ms == expected_ms
        assert len(warnings) == 1 and "line 2" in warnings[0]

    def test_load_ledger_optional_numeric_px(self, tmp_path: Path) -> None:
        line = self._good_line() | {"entry_px": 58100.0}
        p = tmp_path / "calls.jsonl"
        p.write_text(json.dumps(line) + "\n", encoding="utf-8")
        calls, _ = load_ledger(p)
        assert calls[0].entry_px == 58100.0
        assert calls[0].stop_px is None

    def test_load_overrides_and_missing_file(self, tmp_path: Path) -> None:
        p = tmp_path / "overrides.jsonl"
        p.write_text(
            json.dumps(
                {"url": "https://x.com/A/status/1", "stop_px": 56900.0, "skip": False}
            )
            + "\n"
            + json.dumps(
                {"url": "https://x.com/B/status/2", "skip": True, "note": "dup"}
            )
            + "\n",
            encoding="utf-8",
        )
        ov = load_overrides(p)
        assert ov["https://x.com/A/status/1"].stop_px == 56900.0
        assert ov["https://x.com/B/status/2"].skip is True
        assert load_overrides(tmp_path / "absent.jsonl") == {}
