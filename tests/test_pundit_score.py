"""Tests for tools/pundit_score.py — pundit-ledger scorer."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from analytics.store.market_data import upsert_ohlcv
from analytics.store.schema import init_schema
from tools.pundit_score import (
    AUDIT_ELIGIBLE_N,
    CellStats,
    LedgerCall,
    Override,
    ScoredCall,
    _cell_dict,
    _cell_table,
    aggregate,
    audit_eligible_cells,
    build_parser,
    build_priors,
    find_call_candle,
    find_fill,
    load_ledger,
    load_ohlcv_for_calls,
    load_overrides,
    main,
    parse_level_field,
    relay_dependent_authors,
    render_report,
    resolve_levels,
    score_call,
    tag_family,
    window_ms,
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

    def test_leading_negation_with_incidental_number_is_unspecified(self) -> None:
        """A field whose head says NO level was given carries no level.

        The trailing number is incidental context -- a spot-price reference, a
        fib ratio, a scenario index. Harvesting it fabricates a precise call the
        pundit never made, and the sanity gate cannot catch the worst form
        because the phantom number IS the reference close. All strings below are
        verbatim from ``docs/plans/pundit-calls.jsonl``.
        """
        for text in (
            "Not specified -- the video gives no explicit entry (~64,017.6)",
            "unspecified (~58,872, already broke below consolidation)",
            "unspecified (implied above consolidation range ~80,000)",
            "unspecified (implied below ~44,210 / 0.5 fib)",
            "unspecified (bounce toward 73k+)",
            "No explicit target given; implied continuation toward 66,500-66,900",
            "not given (roughly 64k)",
            "n/a (describes the trigger itself); the 63,000-63,500 zone IS it",
        ):
            p = parse_level_field(text)
            assert p.unspecified, repr(text)
            assert p.numbers == (), repr(text)
            assert p.zones == (), repr(text)

    def test_leading_negation_drops_scenario_indices(self) -> None:
        """'scenario 1 / scenario 2' harvested as prices 1.0 and 4.0."""
        p = parse_level_field(
            "none yet - scenario 1: H4 MSB at 60.9K then plan entry; scenario 2: fr"
        )
        assert p.unspecified
        assert p.numbers == ()

    def test_trailing_hedge_keeps_the_level_but_marks_it_hedged(self) -> None:
        """A stated level with a hedged PROVENANCE note is still a real level.

        Distinct from the leading-negation class: here the negation qualifies
        where the level came from, not whether one exists. Dropping it would
        lose a genuine call, so the level survives and is flagged instead.
        """
        p = parse_level_field("chart invalidation line 67.75 (not stated in text)")
        assert not p.unspecified
        assert 67.75 in p.numbers
        assert p.hedged

    def test_plain_level_is_not_hedged(self) -> None:
        assert not parse_level_field("$61,696.80").hedged
        assert not parse_level_field("57,900-58,200").hedged


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

    def test_load_ledger_warns_on_malformed_call_ts_utc(self, tmp_path: Path) -> None:
        bad_line = self._good_line() | {"call_ts_utc": "not-a-date"}
        p = tmp_path / "calls.jsonl"
        p.write_text(
            json.dumps(self._good_line()) + "\n" + json.dumps(bad_line) + "\n",
            encoding="utf-8",
        )
        calls, warnings = load_ledger(p)
        assert len(calls) == 1
        assert calls[0].author == "A"
        assert len(warnings) == 1 and "line 2" in warnings[0]

    def test_load_ledger_skips_direction_outside_the_enum(self, tmp_path: Path) -> None:
        """A fourth direction value must be SKIPPED, never scored.

        ``score_call`` computes ``dirsign = 1.0 if direction == "long" else
        -1.0``, so before this guard *any* unknown value fell through to the
        short branch with nothing raising. Measured 2026-08-05: pass 2 emitted
        ``"range"`` for a range-trade plan, which would have booked a
        deliberately non-directional call as bearish.
        """
        bad_line = self._good_line() | {"direction": "range"}
        p = tmp_path / "calls.jsonl"
        p.write_text(
            json.dumps(self._good_line()) + "\n" + json.dumps(bad_line) + "\n",
            encoding="utf-8",
        )
        calls, warnings = load_ledger(p)
        assert [c.direction for c in calls] == ["long"]
        assert len(warnings) == 1 and "line 2" in warnings[0]
        assert "range" in warnings[0]

    def test_load_ledger_skips_missing_direction(self, tmp_path: Path) -> None:
        """A missing direction is the same bug as an unknown one.

        It arrives as ``""`` and is not ``"long"``, so it also scored as a
        SHORT. Absence must not read as a bearish call.
        """
        bad_line = {k: v for k, v in self._good_line().items() if k != "direction"}
        p = tmp_path / "calls.jsonl"
        p.write_text(
            json.dumps(self._good_line()) + "\n" + json.dumps(bad_line) + "\n",
            encoding="utf-8",
        )
        calls, warnings = load_ledger(p)
        assert [c.direction for c in calls] == ["long"]
        assert len(warnings) == 1 and "line 2" in warnings[0]

    def test_load_ledger_accepts_direction_case_variants(self, tmp_path: Path) -> None:
        """Casing is decoration, not a fourth value — fold it, don't drop it."""
        line = self._good_line() | {"direction": "SHORT"}
        p = tmp_path / "calls.jsonl"
        p.write_text(json.dumps(line) + "\n", encoding="utf-8")
        calls, warnings = load_ledger(p)
        assert [c.direction for c in calls] == ["short"]
        assert warnings == []

    def test_load_ledger_skips_horizon_outside_the_enum(self, tmp_path: Path) -> None:
        """An unrecognised horizon must be SKIPPED, never silently re-windowed.

        ``window_ms`` was ``WINDOWS_MS.get(horizon, WINDOWS_MS["unspecified"])``,
        so a typo bought the 14-day window instead of intraday's 48h or
        swing's 30d — a different WIN/LOSS/NOT_TRIGGERED verdict for the same
        call, with nothing raising.
        """
        bad_line = self._good_line() | {"horizon": "scalp"}
        p = tmp_path / "calls.jsonl"
        p.write_text(
            json.dumps(self._good_line()) + "\n" + json.dumps(bad_line) + "\n",
            encoding="utf-8",
        )
        calls, warnings = load_ledger(p)
        assert [c.horizon for c in calls] == ["swing"]
        assert len(warnings) == 1 and "line 2" in warnings[0]
        assert "scalp" in warnings[0]

    def test_load_ledger_keeps_a_missing_horizon_as_unspecified(
        self, tmp_path: Path
    ) -> None:
        """THE difference from the direction guard — absence is legitimate here.

        ``unspecified`` is a member of the enum, not a fallback for it: a
        pundit who states no timeframe has still made a scoreable call. Four
        of the 174 live rows say so explicitly, and rejecting absence would
        drop them.
        """
        line = {k: v for k, v in self._good_line().items() if k != "horizon"}
        p = tmp_path / "calls.jsonl"
        p.write_text(json.dumps(line) + "\n", encoding="utf-8")
        calls, warnings = load_ledger(p)
        assert [c.horizon for c in calls] == ["unspecified"]
        assert warnings == []

    def test_load_ledger_accepts_horizon_case_variants(self, tmp_path: Path) -> None:
        """Positive control: the guard must not be satisfied by dropping rows."""
        line = self._good_line() | {"horizon": " Intraday "}
        p = tmp_path / "calls.jsonl"
        p.write_text(json.dumps(line) + "\n", encoding="utf-8")
        calls, warnings = load_ledger(p)
        assert [c.horizon for c in calls] == ["intraday"]
        assert warnings == []

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

    def test_hedged_entry_near_ref_close_is_a_thesis_not_a_precise_call(self) -> None:
        """The filed 8a defect, at the layer where it does harm.

        The phantom number IS the reference close, so the sanity gate passes it
        and the entry price barely moves -- but the CONFIDENCE label is the whole
        point: 'ok' asserts the pundit named 58,010, 'fallback' says we scored a
        thesis from the reference close. Only the second is true.
        """
        lv = resolve_levels(
            _call(entry="unspecified (~58,010 at post)", stop="", target=""),
            None,
            self.REF,
        )
        assert lv.entry_is_thesis and lv.entry_px == self.REF
        assert lv.parse_confidence == "fallback"

    def test_hedged_stop_and_target_are_dropped_not_invented(self) -> None:
        """Here the numeric effect is large, not cosmetic."""
        lv = resolve_levels(
            _call(
                entry="58,000",
                stop="unspecified (implied above consolidation range ~59,000)",
                target="No explicit target given; implied toward 60,500-60,900",
            ),
            None,
            self.REF,
        )
        assert lv.stop_px is None
        assert lv.target_px is None

    def test_hedged_provenance_keeps_level_at_low_confidence(self) -> None:
        lv = resolve_levels(
            _call(entry="58,100 (not stated in text, read off the chart)"),
            None,
            self.REF,
        )
        assert lv.entry_px == 58100.0
        assert not lv.entry_is_thesis
        assert lv.parse_confidence == "low"


class TestTagFamily:
    def test_one_case_per_family(self) -> None:
        cases = {
            "sweep range low then reclaim": "sweep_reclaim",
            "rotation toward the composite POC": "vp_level",
            "PDL is the trigger": "ref_level",
            "holding the 1W 50EMA": "ema_trend",
            "CVD remains heavy, absorption at lows": "flow",
            "spot-demand accumulation zone below": "accumulation_zone",
            "break of $81 would be very positive": "breakout_deviation",
            "just vibes": "other",
        }
        for text, family in cases.items():
            assert tag_family(text) == family, text

    def test_priority_order_first_match_wins(self) -> None:
        # 'sweep' outranks 'poc' because sweep_reclaim is listed first.
        assert tag_family("sweep into the POC") == "sweep_reclaim"

    def test_space_wrapped_keyword_hits_at_left_edge(self) -> None:
        # ' oi ' (flow) is space-delimited on both sides in FAMILY_KEYWORDS;
        # a left-boundary-only check anchored on the match's leading space
        # would reject this because the preceding character is the last
        # letter of "reported" — must still hit.
        assert tag_family("reported oi levels are climbing") == "flow"

    def test_keyword_prefix_of_longer_word_does_not_hit(self) -> None:
        # 'val ' (vp_level) is a strict prefix of 'value'; the right-boundary
        # guard must reject the embedded match so this stays untagged.
        assert tag_family("the value of this setup is unclear") == "other"


T0 = 1_781_949_600_000  # 2026-06-20T10:00:00Z


def _candles(
    prices: list[tuple[float, float, float, float]], start_ms: int = T0
) -> pd.DataFrame:
    """1h OHLC frames from (open, high, low, close) tuples."""
    return pd.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "open_time": start_ms + i * 3_600_000,
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": 1.0,
            }
            for i, (o, h, lo, c) in enumerate(prices)
        ]
    )


class TestWindowsAndFill:
    def test_window_ms_mapping(self) -> None:
        assert window_ms("intraday") == 48 * 3_600_000
        assert window_ms("swing") == 30 * 86_400_000
        assert window_ms("unspecified") == 14 * 86_400_000
        assert window_ms("weird") == 14 * 86_400_000

    def test_find_call_candle(self) -> None:
        df = _candles([(100, 110, 90, 105)] * 3)
        assert find_call_candle(df, T0) == 0
        assert find_call_candle(df, T0 + 90 * 60 * 1000) == 1  # mid-candle
        assert find_call_candle(df, T0 - 1) is None
        assert find_call_candle(df, T0 + 3 * 3_600_000) is None  # past data end

    def test_thesis_fill_at_call_close(self) -> None:
        df = _candles([(100, 110, 90, 105), (105, 120, 100, 115)])
        assert find_fill(df, 0, 105.0, True, T0 + 10 * 3_600_000) == (0, 105.0)

    def test_level_fill_on_first_touch_after_call(self) -> None:
        df = _candles([(100, 110, 90, 105), (105, 108, 101, 102), (102, 106, 95, 96)])
        # entry 98 first trades inside candle 2 (low 95).
        assert find_fill(df, 0, 98.0, False, T0 + 10 * 3_600_000) == (2, 98.0)

    def test_level_fill_respects_deadline(self) -> None:
        df = _candles([(100, 110, 90, 105), (105, 108, 101, 102), (102, 106, 95, 96)])
        # deadline before candle 2 opens -> no fill.
        assert find_fill(df, 0, 98.0, False, T0 + 3_600_000) is None


def _score(
    call: LedgerCall,
    df_1h: pd.DataFrame,
    as_of_ms: int,
    override: Override | None = None,
) -> ScoredCall:
    df_1d = _candles([(100, 110, 90, 105)] * 20, start_ms=T0 - 20 * 86_400_000)
    df_1d["open_time"] = [T0 - (20 - i) * 86_400_000 for i in range(20)]
    df_1d["timeframe"] = "1d"
    return score_call(call, override, df_1h, df_1d, as_of_ms)


FAR = T0 + 40 * 86_400_000  # as_of far beyond every swing window


class TestScoreCall:
    def test_win_target_hit_with_stop_gives_rr(self) -> None:
        call = _call(entry="100", stop="90", target="120", horizon="intraday")
        df = _candles([(100, 101, 99, 100), (100, 100, 99, 100), (100, 125, 98, 120)])
        sc = _score(call, df, FAR)
        assert sc.state == "WIN" and sc.win is True
        assert sc.r == 2.0  # (120-100)/(100-90)
        assert sc.fill_px == 100.0 and sc.exit_px == 120.0

    def test_adverse_first_same_bar_is_loss(self) -> None:
        call = _call(entry="100", stop="90", target="120", horizon="intraday")
        df = _candles([(100, 101, 99, 100), (100, 130, 85, 110)])
        sc = _score(call, df, FAR)
        assert sc.state == "LOSS" and sc.r == -1.0

    def test_stop_no_target_expiry_exit_scales_by_risk(self) -> None:
        call = _call(entry="100", stop="95", target="unspecified", horizon="intraday")
        # 50 candles; entry touches candle 1; window 48h from fill; exit at expiry close 104.
        rows: list[tuple[float, float, float, float]] = [
            (100.0, 101.0, 99.0, 100.0)
        ] + [(100.0, 104.0, 99.0, 104.0)] * 50
        sc = _score(call, _candles(rows), FAR)
        assert sc.state == "WIN"
        assert sc.r is not None and abs(sc.r - 0.8) < 1e-9  # (104-100)/5

    def test_no_stop_uses_atr_proxy_and_sign(self) -> None:
        call = _call(entry="100", stop="", target="", horizon="intraday")
        # 20 warm-up candles BEFORE the call so ATR14 has closed 1h history at fill
        # (mirrors load_ohlcv_for_calls' 20-candle back-buffer).
        rows: list[tuple[float, float, float, float]] = [
            (100.0, 101.0, 99.0, 100.0)
        ] * 21 + [(100.0, 101.0, 95.0, 96.0)] * 50
        sc = _score(call, _candles(rows, start_ms=T0 - 20 * 3_600_000), FAR)
        assert sc.state == "LOSS" and sc.r is None
        assert sc.atr_r is not None and sc.atr_r < 0

    def test_short_direction_win(self) -> None:
        call = _call(
            direction="short", entry="100", stop="110", target="80", horizon="intraday"
        )
        df = _candles([(100, 101, 99, 100), (100, 102, 75, 80)])
        sc = _score(call, df, FAR)
        assert sc.state == "WIN" and sc.r == 2.0  # (100-80)/(110-100)

    def test_open_in_position_before_expiry(self) -> None:
        call = _call(entry="100", stop="90", target="120", horizon="swing")
        df = _candles([(100, 101, 99, 100), (100, 101, 99, 100)])
        sc = _score(call, df, T0 + 2 * 3_600_000)
        assert sc.state == "OPEN" and sc.note == "in position"

    def test_open_awaiting_trigger(self) -> None:
        call = _call(entry="90", stop="85", target="120", horizon="swing")
        df = _candles([(100, 101, 99, 100), (100, 101, 99, 100)])
        sc = _score(call, df, T0 + 2 * 3_600_000)
        assert sc.state == "OPEN" and sc.note == "awaiting trigger"

    def test_not_triggered_after_deadline(self) -> None:
        call = _call(entry="90", stop="85", target="120", horizon="intraday")
        rows: list[tuple[float, float, float, float]] = [
            (100, 101, 99, 100)
        ] * 60  # 60h of candles never touching 90
        sc = _score(call, _candles(rows), FAR)
        assert sc.state == "NOT_TRIGGERED"

    def test_stale_when_data_ends_mid_window(self) -> None:
        call = _call(entry="100", stop="90", target="120", horizon="swing")
        df = _candles(
            [(100, 101, 99, 100), (100, 101, 99, 100)]
        )  # 2h of data, 30d window
        sc = _score(call, df, FAR)
        assert sc.state == "STALE"

    def test_neutral_unscored_and_skip(self) -> None:
        df = _candles([(100, 101, 99, 100)])
        assert _score(_call(direction="neutral"), df, FAR).state == "UNSCORED"
        ov = Override(url="https://x.com/A/status/1", skip=True)
        assert _score(_call(), df, FAR, override=ov).state == "SKIPPED"

    def test_unresolvable_without_data(self) -> None:
        sc = _score(_call(), _candles([]), FAR)
        assert sc.state == "UNRESOLVABLE"


def _scored_fixture() -> list[ScoredCall]:
    df = _candles([(100, 101, 99, 100), (100, 100, 99, 100), (100, 125, 98, 120)])
    win = _score(
        _call(entry="100", stop="90", target="120", horizon="intraday"), df, FAR
    )
    loss_df = _candles([(100, 101, 99, 100), (100, 130, 85, 110)])
    loss = _score(
        _call(
            author="B",
            url="https://x.com/B/status/2",
            entry="100",
            stop="90",
            target="120",
            horizon="intraday",
            raw_quote="POC rotation",
        ),
        loss_df,
        FAR,
    )
    open_df = _candles([(100, 101, 99, 100), (100, 101, 99, 100)])
    open_ = _score(
        _call(
            author="A",
            url="https://x.com/A/status/3",
            entry="100",
            stop="90",
            target="120",
            horizon="swing",
        ),
        open_df,
        T0 + 2 * 3_600_000,
    )
    return [win, loss, open_]


class TestAggregateAndOutputs:
    def test_aggregate_per_author(self) -> None:
        cells = aggregate(_scored_fixture(), lambda sc: sc.call.author)
        a, b = cells["A"], cells["B"]
        assert (a.n, a.triggered, a.open_, a.resolved, a.wins) == (2, 2, 1, 1, 1)
        assert a.hit_rate == 1.0 and a.avg_r == 2.0
        assert (b.n, b.resolved, b.wins) == (1, 1, 0)
        assert b.avg_r == -1.0

    def test_audit_eligible_cells_is_inclusive_at_the_threshold(self) -> None:
        # `>=`, not `>`. The whole point is to notice the crossing, and an
        # off-by-one here would delay the notice by one observation forever.
        cells = {
            "under": CellStats(n=AUDIT_ELIGIBLE_N - 1),
            "at": CellStats(n=AUDIT_ELIGIBLE_N),
            "over": CellStats(n=AUDIT_ELIGIBLE_N + 5),
        }
        assert audit_eligible_cells(cells) == ["at", "over"]

    def test_audit_eligible_cells_empty_when_nothing_qualifies(self) -> None:
        assert audit_eligible_cells({"a": CellStats(n=1)}) == []

    def test_report_notes_the_crossing_and_says_no_gate_fires(self) -> None:
        # 15 copies of the fixture puts author A at n=30 -- exactly the boundary.
        scored = _scored_fixture() * 15
        report = render_report(scored, [], "2026-07-04T00:00:00Z", 5)
        assert f"n≥{AUDIT_ELIGIBLE_N}" in report
        # It must say plainly that nothing fires. This notice replaced a
        # docstring that implied a live n>=30 gate; restating the same false
        # promise in the report would just move the defect.
        assert "No gate is implemented here and none fires" in report
        assert "A" in report

    def test_report_is_silent_when_no_cell_has_crossed(self) -> None:
        # Negative control: without this, a NOTE printed unconditionally would
        # pass the test above while telling the operator nothing.
        report = render_report(_scored_fixture(), [], "2026-07-04T00:00:00Z", 5)
        assert f"n≥{AUDIT_ELIGIBLE_N}" not in report

    def test_render_report_sections_and_audit_trail(self) -> None:
        report = render_report(
            _scored_fixture(), ["ledger line 9: skipped"], "2026-07-04T00:00:00Z", 5
        )
        assert "## Per author" in report
        assert "## Per setup-family" in report
        assert "## Audit trail" in report
        assert "ledger line 9" in report
        assert "OPEN" in report and "WIN" in report and "LOSS" in report
        assert "⚠" in report  # n<5 marker present at this tiny n
        # Finding 1 (spec §Outputs): explicit wins/losses columns, not just `resolved`.
        # ATR-R leads avg R from 2026-08-11: it is the COMPLETE sample, where
        # avg R is computed only over calls that stated a stop and so carries
        # its own (r_n/resolved) denominator. See CellStats.r_coverage.
        assert (
            "| author | n | trig | open | resolved | wins | losses "
            "| hit% | avg ATR-R | avg R (cov) | |" in report
        )
        # cell A: n=2, triggered=2, open=1, resolved=1, wins=1, losses=resolved-wins=0.
        assert "| A | 2 | 2 | 1 | 1 | 1 | 0 |" in report
        # cell B: n=1, triggered=1, open=0, resolved=1, wins=0, losses=1.
        assert "| B | 1 | 1 | 0 | 1 | 0 | 1 |" in report
        # every markdown table (header/delimiter/every data row) has a uniform
        # column count — a mismatch is a real rendering defect.
        table: list[str] = []
        for line in [*report.splitlines(), ""]:
            if line.startswith("|"):
                table.append(line)
            elif table:
                counts = {ln.count("|") for ln in table}
                assert len(counts) == 1, table
                table = []

    def test_build_priors_schema_and_determinism(self) -> None:
        scored = _scored_fixture()
        p1 = build_priors(scored, "2026-07-04T00:00:00Z", "2026-07-04T09:00:00Z", 5)
        p2 = build_priors(scored, "2026-07-04T00:00:00Z", "2026-07-04T09:00:00Z", 5)
        assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)
        assert p1["as_of"] == "2026-07-04T00:00:00Z"
        assert p1["policy"]["windows"]["intraday"] == "48h"  # type: ignore[index]
        authors = p1["authors"]
        assert isinstance(authors, dict) and authors["A"]["n"] == 2
        # Finding 2 (spec §Outputs, binding contract with analytics/brief/pundit.py
        # on feat/market-brief): families is NESTED {family: {direction: stats}},
        # never a flat "family/direction" key.
        families = p1["families"]
        assert isinstance(families, dict)
        for fam_key in families:
            assert "/" not in fam_key, "families must be nested, not 'family/direction'"
        other = families["other"]
        assert isinstance(other, dict)
        assert other["long"]["n"] == 2  # win + open_, both family=other/direction=long
        vp_level = families["vp_level"]
        assert isinstance(vp_level, dict)
        assert vp_level["long"]["n"] == 1  # loss call, raw_quote "POC rotation"
        assert vp_level["long"]["hit_rate"] == 0.0


class TestDbAndCli:
    def test_load_ohlcv_for_calls_in_memory(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        df = _candles([(100, 110, 90, 105)] * 30)
        df["taker_buy_volume"] = 0.5  # upsert_ohlcv requires the full 9-column list
        upsert_ohlcv(conn, df, venue="binance")
        d1 = df.copy()
        d1["timeframe"] = "1d"
        upsert_ohlcv(conn, d1, venue="binance")
        calls = [_call()]
        data = load_ohlcv_for_calls(conn, calls, FAR)
        assert not data[("BTCUSDT", "1h")].empty
        assert not data[("BTCUSDT", "1d")].empty
        assert list(data[("BTCUSDT", "1h")]["open_time"]) == sorted(
            data[("BTCUSDT", "1h")]["open_time"]
        )

    def test_build_parser_defaults(self) -> None:
        args = build_parser().parse_args([])
        assert args.ledger == Path("docs/plans/pundit-calls.jsonl")
        assert args.overrides == Path("docs/plans/pundit-overrides.jsonl")
        assert args.json == Path("docs/plans/pundit-priors.json")
        assert args.min_n == 5
        assert args.as_of is None

    def test_parse_as_of(self) -> None:
        args = build_parser().parse_args(["--as-of", "2026-07-04T00:00:00Z"])
        assert args.as_of == "2026-07-04T00:00:00Z"


class TestAttributionWiring:
    """The read half of relay attribution — see analytics/pundit_attribution.py.

    Both ledger fields were written by the ingest skills from the day the
    feature shipped and dropped on the floor at this boundary, so a handle the
    roster *asserted* scored at identical trust to one who spoke for himself.
    """

    def _relay_line(self, **kw: object) -> dict[str, object]:
        line: dict[str, object] = {
            "source": "youtube",
            "author": "sanmage88",
            "url": "https://youtu.be/abc",
            "call_ts_utc": "2026-06-20T10:00:00Z",
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry": "58,000",
            "stop": "57,000",
            "target": "60,000",
            "horizon": "swing",
            "confidence": "",
            "raw_quote": "",
            "attribution": "relay",
            "relayed_by": "@KoluniteVIP",
            "attribution_confidence": "operator",
        }
        line.update(kw)
        return line

    def test_load_ledger_reads_both_fields(self, tmp_path: Path) -> None:
        p = tmp_path / "calls.jsonl"
        p.write_text(json.dumps(self._relay_line()) + "\n", encoding="utf-8")
        calls, warnings = load_ledger(p)
        assert warnings == []
        assert calls[0].attribution == "relay"
        assert calls[0].attribution_confidence == "operator"

    def test_a_pre_feature_row_defaults_to_trusted_first_hand(
        self, tmp_path: Path
    ) -> None:
        # 170 of the 203 live rows carry neither field. They must keep scoring
        # exactly as before, or this commit silently moves published priors.
        line = self._relay_line()
        for key in ("attribution", "relayed_by", "attribution_confidence"):
            del line[key]
        p = tmp_path / "calls.jsonl"
        p.write_text(json.dumps(line) + "\n", encoding="utf-8")
        calls, warnings = load_ledger(p)
        assert warnings == []
        assert calls[0].attribution == "first-hand"
        assert calls[0].attribution_confidence == ""

    def test_relayed_by_alone_marks_the_row_relay(self, tmp_path: Path) -> None:
        # The fail-safe, exercised through the real read boundary: omitting
        # `attribution` on a genuine relay row must not buy full trust.
        line = self._relay_line()
        del line["attribution"]
        p = tmp_path / "calls.jsonl"
        p.write_text(json.dumps(line) + "\n", encoding="utf-8")
        calls, _ = load_ledger(p)
        assert calls[0].attribution == "relay"
        assert calls[0].attribution_confidence == "operator"

    def test_an_out_of_enum_attribution_warns_rather_than_crashes(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "calls.jsonl"
        p.write_text(
            json.dumps(self._relay_line(attribution="secondhand")) + "\n",
            encoding="utf-8",
        )
        calls, warnings = load_ledger(p)
        assert calls == []
        assert len(warnings) == 1 and "secondhand" in warnings[0]

    def test_an_out_of_enum_confidence_warns_rather_than_crashes(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "calls.jsonl"
        p.write_text(
            json.dumps(self._relay_line(attribution_confidence="verified")) + "\n",
            encoding="utf-8",
        )
        calls, warnings = load_ledger(p)
        assert calls == []
        assert len(warnings) == 1 and "verified" in warnings[0]


class TestRelayDependentAuthors:
    def _sc(self, author: str, attribution: str, conf: str) -> ScoredCall:
        return ScoredCall(
            call=_call(
                author=author,
                attribution=attribution,
                attribution_confidence=conf,
            ),
            levels=None,
            family="other",
            state="UNSCORED",
        )

    def test_flags_an_author_whose_every_row_is_operator_relay(self) -> None:
        # The live shape: eleven authors of 1-4 rows each, none with a
        # first-hand row, all at "operator". Revoke the entry, they vanish.
        scored = [
            self._sc("phantom", "relay", "operator"),
            self._sc("phantom", "relay", "operator"),
        ]
        assert relay_dependent_authors(scored) == {"phantom": 2}

    def test_ignores_an_author_with_any_first_hand_row(self) -> None:
        # Partly, not wholly: a wrong roster entry would corrupt part of this
        # record but could not invent the person.
        scored = [
            self._sc("mixed", "relay", "operator"),
            self._sc("mixed", "first-hand", ""),
        ]
        assert relay_dependent_authors(scored) == {}

    def test_ignores_an_author_relayed_at_high_confidence(self) -> None:
        scored = [self._sc("corroborated", "relay", "high")]
        assert relay_dependent_authors(scored) == {}

    def test_unknown_confidence_counts_as_unverified(self) -> None:
        # Absent confidence ranks BELOW operator, so it must not escape the net.
        scored = [self._sc("silent", "relay", "")]
        assert relay_dependent_authors(scored) == {"silent": 1}

    def test_empty_ledger_yields_nothing(self) -> None:
        assert relay_dependent_authors([]) == {}

    def test_report_names_the_authors_and_the_escape_hatch(self) -> None:
        scored = [self._sc("phantom", "relay", "operator")]
        out = render_report(scored, [], "2026-08-08T00:00:00Z", 5)
        assert "phantom (n=1)" in out
        assert "--min-attribution-confidence high" in out

    def test_report_stays_silent_when_nothing_is_relay_dependent(self) -> None:
        out = render_report(_scored_fixture(), [], "2026-08-08T00:00:00Z", 5)
        assert "relay attribution" not in out


class TestAttributionFilterCLI:
    def test_default_floor_is_any(self) -> None:
        # The whole point: this commit moves no published number on its own.
        assert build_parser().parse_args([]).min_attribution_confidence == "any"

    def test_floor_is_restricted_to_the_known_levels(self) -> None:
        args = build_parser().parse_args(["--min-attribution-confidence", "high"])
        assert args.min_attribution_confidence == "high"
        with pytest.raises(SystemExit):
            build_parser().parse_args(["--min-attribution-confidence", "verified"])

    def test_filtered_run_refuses_to_overwrite_the_published_priors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        # Same defect shape as the `--capital` drawdown-peak poisoning: an
        # exploratory knob silently replacing a durable artifact the Brief
        # reads. Refuse, do not warn - a warning arrives after the write.
        #
        # `--ledger` is passed explicitly even though the refusal fires before
        # any read: the default is gitignored `docs/plans/pundit-calls.jsonl`,
        # which exists on a dev box and NOT in CI. The first version of this
        # test relied on the default and passed locally while failing in CI --
        # a hermeticity bug that only a machine without the file can see.
        ledger = tmp_path / "calls.jsonl"
        ledger.write_text("", encoding="utf-8")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "pundit_score",
                "--min-attribution-confidence",
                "high",
                "--ledger",
                str(ledger),
            ],
        )
        assert main() == 2
        assert "refusing to overwrite" in capsys.readouterr().err

    def test_the_refusal_precedes_any_file_read(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Pins the ordering directly: with a ledger path that does not exist,
        # the refusal must still be what happens -- exit 2, not a
        # FileNotFoundError. Fails fast on a bad flag pair, and keeps the guard
        # reachable on a machine that has no ledger at all.
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "pundit_score",
                "--min-attribution-confidence",
                "high",
                "--ledger",
                "/nonexistent/does-not-exist.jsonl",
            ],
        )
        assert main() == 2
        assert "refusing to overwrite" in capsys.readouterr().err

    def test_an_explicit_json_path_is_allowed_to_filter(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The refusal must be about the DESTINATION, not about filtering, or
        # the flag would be unusable for the what-if it exists to answer.
        ledger = tmp_path / "calls.jsonl"
        ledger.write_text("", encoding="utf-8")
        db = tmp_path / "empty.db"
        with duckdb.connect(str(db)) as conn:
            init_schema(conn)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "pundit_score",
                "--min-attribution-confidence",
                "high",
                "--ledger",
                str(ledger),
                "--json",
                str(tmp_path / "what-if.json"),
                "--db",
                str(db),
            ],
        )
        assert main() == 0
        assert (tmp_path / "what-if.json").exists()


def _cov_sc(state: str, r: float | None, atr_r: float | None, win: bool) -> ScoredCall:
    """ScoredCall carrying only what a cell roll-up reads. Reuses `_call`."""
    return ScoredCall(
        call=_call(),
        levels=None,
        family="other",
        state=state,
        r=r,
        atr_r=atr_r,
        win=win,
    )


class TestRCoverageDisclosure:
    """`avg_r` is computed only over calls that stated a stop, and WINNERS are
    the ones that disproportionately lack one — so `avg_r` silently describes a
    loss-enriched subsample while `n` describes the whole cell.

    Every fixture here is deliberately ASYMMETRIC (a win with no R beside a loss
    with one). A fixture where every call carries an R would make these
    assertions pass against the censored code too.
    """

    @staticmethod
    def _censored_cell() -> CellStats:
        c = CellStats()
        c.add(_cov_sc("WIN", None, 2.0, True))  # the dropped winner
        c.add(_cov_sc("LOSS", -1.0, -0.5, False))
        c.add(_cov_sc("WIN", 1.0, 1.0, True))
        return c

    def test_coverage_is_below_one_when_a_winner_lacks_a_stop(self) -> None:
        c = self._censored_cell()
        assert c.resolved == 3
        assert c.r_n == 2
        assert c.r_coverage == pytest.approx(2 / 3)

    def test_censoring_moves_avg_r_away_from_avg_atr_r(self) -> None:
        """The defect made visible: same three calls, two different answers."""
        c = self._censored_cell()
        avg_r, avg_atr_r = c.avg_r, c.avg_atr_r
        assert avg_r is not None and avg_atr_r is not None
        assert avg_r == pytest.approx(0.0)  # (-1.0 + 1.0) / 2
        assert avg_atr_r == pytest.approx((2.0 - 0.5 + 1.0) / 3)
        assert avg_r < avg_atr_r  # censoring biases avg_r DOWN

    def test_full_coverage_reports_one(self) -> None:
        c = CellStats()
        c.add(_cov_sc("WIN", 1.0, 1.0, True))
        c.add(_cov_sc("LOSS", -1.0, -1.0, False))
        assert c.r_coverage == pytest.approx(1.0)

    def test_coverage_is_none_with_nothing_resolved(self) -> None:
        c = CellStats()
        c.add(_cov_sc("OPEN", None, None, False))
        assert c.r_coverage is None

    def test_cell_dict_publishes_the_denominator(self) -> None:
        d = _cell_dict(self._censored_cell())
        assert d["r_n"] == 2
        assert d["resolved"] == 3
        assert d["r_coverage"] == pytest.approx(2 / 3)
        assert d["atr_r_n"] == 3

    def test_cell_table_shows_coverage_beside_avg_r(self) -> None:
        rows = _cell_table({"A": self._censored_cell()}, "author", min_n=1)
        body = rows[-1]
        assert "(2/3)" in body, f"coverage not disclosed in report row: {body}"

    def test_priors_json_carries_coverage(self) -> None:
        priors = build_priors(
            [
                _cov_sc("WIN", None, 2.0, True),
                _cov_sc("LOSS", -1.0, -0.5, False),
            ],
            "2026-08-11T00:00:00Z",
            "2026-08-11T00:00:00Z",
            min_n=1,
        )
        authors = priors["authors"]
        assert isinstance(authors, dict)
        cell = authors["A"]
        assert isinstance(cell, dict)
        assert cell["r_coverage"] == pytest.approx(0.5)
        assert cell["r_n"] == 1
