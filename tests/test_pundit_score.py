"""Tests for tools/pundit_score.py — pundit-ledger scorer."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tools.pundit_score import (
    LedgerCall,
    Override,
    load_ledger,
    load_overrides,
    parse_level_field,
    resolve_levels,
)


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

    def test_load_ledger_warns_on_non_object_json_line(self, tmp_path: Path) -> None:
        p = tmp_path / "calls.jsonl"
        p.write_text(
            json.dumps(self._good_line()) + "\n" + json.dumps([1, 2]) + "\n",
            encoding="utf-8",
        )
        calls, warnings = load_ledger(p)
        assert len(calls) == 1
        assert calls[0].author == "A"
        assert len(warnings) == 1 and "line 2" in warnings[0]

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


def _call(**kw: object) -> LedgerCall:
    base: dict[str, object] = {
        "line_no": 1,
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
        "raw_quote": "",
    }
    base.update(kw)
    return LedgerCall(**base)  # type: ignore[arg-type]


class TestResolveLevels:
    REF = 58000.0

    def test_clean_numeric_all_fields_ok(self) -> None:
        lv = resolve_levels(_call(), None, self.REF)
        assert (lv.entry_px, lv.stop_px, lv.target_px) == (58000.0, 57000.0, 60000.0)
        assert lv.parse_confidence == "ok"
        assert not lv.entry_is_thesis

    def test_zone_entry_mid_stop_far_target_near_long(self) -> None:
        lv = resolve_levels(
            _call(entry="57,900-58,200", stop="57,500-57,700", target="59,500-60,700"),
            None,
            self.REF,
        )
        assert lv.entry_px == 58050.0  # zone mid
        assert lv.stop_px == 57500.0  # far edge for a long = lower bound
        assert lv.target_px == 59500.0  # near edge for a long = lower bound

    def test_zone_edges_short(self) -> None:
        lv = resolve_levels(
            _call(
                direction="short",
                entry="58,800-59,000",
                stop="59,200-59,600",
                target="57,000-57,400",
            ),
            None,
            self.REF,
        )
        assert lv.stop_px == 59600.0  # far edge for a short = upper bound
        assert lv.target_px == 57400.0  # near edge for a short = upper bound

    def test_sanity_gate_skips_date_noise(self) -> None:
        lv = resolve_levels(
            _call(target="liquidity below 58K (June 25 low ~58,043)"), None, self.REF
        )
        assert lv.target_px == 58000.0  # '58K' expands; '25' rejected by the gate
        assert lv.parse_confidence == "low"  # multiple sane candidates -> ambiguous

    def test_sanity_gate_rejects_all_falls_back(self) -> None:
        lv = resolve_levels(_call(entry="HTF demand ~38-45"), None, self.REF)
        assert lv.entry_is_thesis and lv.entry_px == self.REF
        assert lv.parse_confidence == "fallback"

    def test_unspecified_entry_thesis_fallback(self) -> None:
        lv = resolve_levels(
            _call(entry="unspecified", stop="", target=""), None, self.REF
        )
        assert lv.entry_is_thesis and lv.entry_px == self.REF
        assert lv.stop_px is None and lv.target_px is None
        assert lv.parse_confidence == "fallback"

    def test_ledger_px_beats_text_and_override_beats_both(self) -> None:
        call = _call(entry="55,000", entry_px=58100.0)
        assert resolve_levels(call, None, self.REF).entry_px == 58100.0
        ov = Override(url=call.url, entry_px=58200.0)
        lv = resolve_levels(call, ov, self.REF)
        assert lv.entry_px == 58200.0
        assert lv.parse_confidence == "override"
