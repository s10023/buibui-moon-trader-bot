"""Tests for tools/route_dedup.py — pure matching + a ledger; no network anywhere."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.route_dedup import (
    MECHANICS_SINK,
    PUNDIT_SINK,
    THESIS_SINK,
    RoutedItem,
    append_routed,
    find_similar,
    is_routed,
    load_ledger,
    main,
    normalize_levels,
    remove_routed,
    split_entries,
)
from tools.x_route import route_target


def _item(
    source_id: str = "vid1",
    item_ts: float = 0.0,
    sink: str = PUNDIT_SINK,
) -> RoutedItem:
    return RoutedItem(
        source_id=source_id,
        item_ts=item_ts,
        sink=sink,
        routed_ts_utc="2026-07-31T12:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# Drift guard: these constants must stay byte-identical to what the shared router
# actually returns, or dedup silently checks the wrong file forever.
# ---------------------------------------------------------------------------


def test_sink_constants_match_the_router() -> None:
    assert route_target("setup", "") == PUNDIT_SINK
    assert route_target("mechanic", "") == MECHANICS_SINK
    assert route_target("claim", "NOVEL") == THESIS_SINK


# ---------------------------------------------------------------------------
# Ledger I/O
# ---------------------------------------------------------------------------


def test_load_ledger_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_ledger(tmp_path / "nope.json") == []


def test_append_then_load_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "routed.json"
    append_routed(path, [_item()])
    assert load_ledger(path) == [_item()]


def test_append_routed_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "routed.json"
    append_routed(path, [_item()])
    append_routed(path, [_item()])
    assert len(load_ledger(path)) == 1


def test_append_routed_leaves_no_tmp_file_behind(tmp_path: Path) -> None:
    path = tmp_path / "routed.json"
    append_routed(path, [_item()])
    assert [p.name for p in tmp_path.iterdir()] == ["routed.json"]


# A silent reset would re-queue everything ever routed, which is precisely the
# duplicate flood this module exists to prevent — same reasoning as yt_feed's state.
def test_malformed_ledger_aborts_loudly(tmp_path: Path) -> None:
    path = tmp_path / "routed.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit, match="malformed"):
        load_ledger(path)


def test_unrecognized_ledger_shape_aborts_loudly(tmp_path: Path) -> None:
    path = tmp_path / "routed.json"
    path.write_text(json.dumps({"version": 999, "items": []}), encoding="utf-8")
    with pytest.raises(SystemExit, match="version"):
        load_ledger(path)


def test_remove_routed_lets_a_deleted_row_be_re_routed(tmp_path: Path) -> None:
    path = tmp_path / "routed.json"
    append_routed(path, [_item()])
    remove_routed(path, source_id="vid1", item_ts=0.0, sink=PUNDIT_SINK)
    assert load_ledger(path) == []


# ---------------------------------------------------------------------------
# Key collisions. Keying on source_id ALONE would silently eat real rows:
# umX9m7y7jsU legitimately produced calls at t=162s AND t=886s.
# ---------------------------------------------------------------------------


def test_same_video_different_timestamps_are_distinct_items() -> None:
    ledger = [_item("umX9m7y7jsU", 162.0)]
    assert is_routed(ledger, "umX9m7y7jsU", 162.0, PUNDIT_SINK)
    assert not is_routed(ledger, "umX9m7y7jsU", 886.0, PUNDIT_SINK)


def test_same_moment_routed_to_different_sinks_are_distinct_items() -> None:
    ledger = [_item("vid1", 30.0, PUNDIT_SINK)]
    assert not is_routed(ledger, "vid1", 30.0, THESIS_SINK)


def test_item_ts_is_matched_at_one_decimal_place() -> None:
    # Float noise off the transcript must not split one item into two ledger rows.
    ledger = [_item("vid1", 162.0)]
    assert is_routed(ledger, "vid1", 162.04, PUNDIT_SINK)


def test_is_routed_touches_no_filesystem(tmp_path: Path) -> None:
    before = list(tmp_path.iterdir())
    is_routed([_item()], "vid1", 0.0, PUNDIT_SINK)
    assert list(tmp_path.iterdir()) == before


# ---------------------------------------------------------------------------
# Level normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("sweep of 69k then reclaim", {69000.0}),
        ("trail to 1,982.10", {1982.1}),
        ("bid ~57000", {57000.0}),
        ("supply $60,946.8", {60946.8}),
        ("range 58,600-58,900", {58600.0, 58900.0}),
    ],
)
def test_normalize_levels_reads_price_forms(text: str, expected: set[float]) -> None:
    assert normalize_levels(text) == expected


# Two negatives that would otherwise make every entry look like every other entry.
def test_iso_dates_are_not_price_levels() -> None:
    assert normalize_levels("## 2026-07-13 — @someone on BTC") == frozenset()


def test_fib_ratios_and_small_numbers_are_not_price_levels() -> None:
    assert (
        normalize_levels("0.618 retrace, 14-bar RSI, 4h chart, 2x size") == frozenset()
    )


# ---------------------------------------------------------------------------
# Sink-shaped splitting
# ---------------------------------------------------------------------------


def test_split_thesis_inbox_on_level_two_headings() -> None:
    text = (
        "# Thesis Inbox\n\npreamble\n\n"
        "## 2026-06-30 — @a on X\n\nbody a\n\n"
        "## 2026-07-01 — @b on Y\n\nbody b\n"
    )
    entries = split_entries(THESIS_SINK, text)
    assert len(entries) == 2
    assert "body a" in entries[0]
    assert "body b" in entries[1]


def test_split_mechanics_on_top_level_bullets_keeping_continuations() -> None:
    text = "# Mechanics\n\nintro\n\n- 2026-06-30 (@a): first rule\n  continued here\n- 2026-07-01 (@b): second rule\n"
    entries = split_entries(MECHANICS_SINK, text)
    assert len(entries) == 2
    assert "continued here" in entries[0]


def test_split_pundit_calls_one_entry_per_line() -> None:
    text = '{"author":"a"}\n\n{"author":"b"}\n'
    assert len(split_entries(PUNDIT_SINK, text)) == 2


# ---------------------------------------------------------------------------
# Similarity. The observed 2026-07-31 case: 大漂亮 restated the ~69k short-term-holder
# cost-basis thesis 18 days after @Max1milianPrice. Routed rows are normalized to
# English, so the sharpest shared feature across that pair was the number.
# ---------------------------------------------------------------------------


_INBOX = """# Thesis Inbox

## 2026-07-13 — @Max1milianPrice on short-term holder cost basis

Short-term holder realized price sits at 69,000; reclaiming it flips the cohort
back into profit and has marked local bottoms historically.
Status: NEW

## 2026-06-30 — @duje_matic on USDT.D dominance

USDT.D weekly pressing the 9.472% multi-year high; reversal marks the cycle bottom.
Status: NEW
"""


def test_find_similar_flags_a_restated_thesis() -> None:
    claim = "short-term holder cost basis around 69k is the line that decides the trend"
    hits = find_similar(claim, THESIS_SINK, _INBOX)
    assert hits
    assert "Max1milianPrice" in hits[0].excerpt
    assert 69000.0 in hits[0].shared_levels


def test_find_similar_ignores_an_unrelated_entry() -> None:
    claim = "liquidation cluster asymmetry above price is a squeeze magnet"
    assert find_similar(claim, THESIS_SINK, _INBOX) == []


def test_find_similar_on_an_empty_sink_is_empty() -> None:
    assert find_similar("anything at all", THESIS_SINK, "") == []


# Two pundits making the same call are two real observations — pundit_score.py scores
# both authors — so collapsing them would destroy signal. Stream C gets identity
# dedup only, and the digest is told the semantic pass did not run.
def test_find_similar_never_runs_on_the_pundit_ledger() -> None:
    line = (
        '{"author":"a","symbol":"BTCUSDT","entry":"69000","raw_quote_en":"69k holds"}'
    )
    assert find_similar("69k cost basis holds", PUNDIT_SINK, line) == []


def test_check_reports_that_stream_c_had_no_semantic_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(
        [
            "check",
            "--source-id",
            "v",
            "--item-ts",
            "0",
            "--sink",
            PUNDIT_SINK,
            "--sink-path",
            str(tmp_path / "absent.jsonl"),
            "--text",
            "69k",
            "--ledger",
            str(tmp_path / "routed.json"),
        ]
    )
    assert json.loads(capsys.readouterr().out)["semantic_checked"] is False


def test_check_reports_a_semantic_pass_on_stream_a(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sink = tmp_path / "thesis-inbox.md"
    sink.write_text(_INBOX, encoding="utf-8")
    main(
        [
            "check",
            "--source-id",
            "v",
            "--item-ts",
            "0",
            "--sink",
            THESIS_SINK,
            "--sink-path",
            str(sink),
            "--text",
            "69k",
            "--ledger",
            str(tmp_path / "routed.json"),
        ]
    )
    assert json.loads(capsys.readouterr().out)["semantic_checked"] is True


def test_find_similar_respects_top_n() -> None:
    claim = "short-term holder cost basis around 69k, and USDT.D dominance too"
    assert len(find_similar(claim, THESIS_SINK, _INBOX, top_n=1)) <= 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_check_reports_not_routed_and_emits_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sink = tmp_path / "thesis-inbox.md"
    sink.write_text(_INBOX, encoding="utf-8")
    rc = main(
        [
            "check",
            "--source-id",
            "vid1",
            "--item-ts",
            "0",
            "--sink",
            THESIS_SINK,
            "--sink-path",
            str(sink),
            "--text",
            "short-term holder cost basis around 69k decides the trend",
            "--ledger",
            str(tmp_path / "routed.json"),
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["already_routed"] is False
    assert payload["candidates"][0]["shared_levels"] == [69000.0]


def test_check_reports_already_routed_after_mark(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "routed.json"
    args = ["--source-id", "vid1", "--item-ts", "0", "--sink", PUNDIT_SINK]
    assert main(["mark", *args, "--ledger", str(ledger)]) == 0
    capsys.readouterr()
    assert main(["check", *args, "--ledger", str(ledger), "--text", "x"]) == 0
    assert json.loads(capsys.readouterr().out)["already_routed"] is True


def test_mark_is_idempotent(tmp_path: Path) -> None:
    ledger = tmp_path / "routed.json"
    args = ["mark", "--source-id", "v", "--item-ts", "1", "--sink", PUNDIT_SINK]
    main([*args, "--ledger", str(ledger)])
    main([*args, "--ledger", str(ledger)])
    assert len(load_ledger(ledger)) == 1


def test_unmark_clears_the_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "routed.json"
    args = ["--source-id", "v", "--item-ts", "1", "--sink", PUNDIT_SINK]
    main(["mark", *args, "--ledger", str(ledger)])
    main(["unmark", *args, "--ledger", str(ledger)])
    capsys.readouterr()
    main(["check", *args, "--ledger", str(ledger), "--text", "x"])
    assert json.loads(capsys.readouterr().out)["already_routed"] is False


def test_check_with_a_missing_sink_file_reports_no_candidates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(
        [
            "check",
            "--source-id",
            "v",
            "--item-ts",
            "0",
            "--sink",
            THESIS_SINK,
            "--sink-path",
            str(tmp_path / "absent.md"),
            "--text",
            "69k cost basis",
            "--ledger",
            str(tmp_path / "routed.json"),
        ]
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["candidates"] == []
