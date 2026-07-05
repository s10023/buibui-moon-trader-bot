"""Tests for tools/pundit_score.py — pundit-ledger scorer."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from tools.pundit_score import (
    LedgerCall,
    Override,
    ScoredCall,
    aggregate,
    build_priors,
    find_call_candle,
    find_fill,
    load_ledger,
    load_overrides,
    parse_level_field,
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

    def test_build_priors_schema_and_determinism(self) -> None:
        scored = _scored_fixture()
        p1 = build_priors(scored, "2026-07-04T00:00:00Z", "2026-07-04T09:00:00Z", 5)
        p2 = build_priors(scored, "2026-07-04T00:00:00Z", "2026-07-04T09:00:00Z", 5)
        assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)
        assert p1["as_of"] == "2026-07-04T00:00:00Z"
        assert p1["policy"]["windows"]["intraday"] == "48h"  # type: ignore[index]
        authors = p1["authors"]
        assert isinstance(authors, dict) and authors["A"]["n"] == 2
        assert "families" in p1
