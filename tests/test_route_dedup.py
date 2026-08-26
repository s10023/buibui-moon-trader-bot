"""Tests for tools/route_dedup.py — pure matching + a ledger; no network anywhere."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tools.route_dedup import (
    KNOWN_SINKS,
    MECHANICS_SINK,
    PUNDIT_SINK,
    THESIS_SINK,
    RoutedItem,
    _comparable_entries,
    append_routed,
    find_similar,
    find_source_duplicate_pairs,
    is_routed,
    load_ledger,
    main,
    normalize_levels,
    parse_source_id,
    remove_routed,
    seed_items,
    semantic_scope,
    split_entries,
)
from tools.x_route import route_target

REPO = Path(__file__).resolve().parent.parent


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


# Stream C's pass is scoped to one source, so on an absent sink there is nothing to
# compare — but the scope is still reported, because "no candidates" and "no pass"
# have to stay distinguishable in the digest.
def test_check_on_an_absent_stream_c_sink_still_reports_its_scope(
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
    out = json.loads(capsys.readouterr().out)
    assert out["semantic_scope"] == "same-source"
    assert out["candidates"] == []


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
# Stream C's same-source blind spot. The exemption is right ACROSS sources — two
# pundits making the same call are two real observations and pundit_score.py scores
# both authors — but it was applied WITHIN one video too. Observed 2026-07-31: two
# items from a single video were the entry leg and the target leg of the SAME open
# long, and both became ledger rows.
# ---------------------------------------------------------------------------

_VIDEO_ID = "abc12345678"
_OTHER_VIDEO_ID = "zyx98765432"


def _call_row(
    *,
    video_id: str = _VIDEO_ID,
    ts: float = 0.0,
    symbol: str = "BTCUSDT",
    direction: str = "long",
    entry: str = "115200",
    stop: str = "113800",
    target: str = "118500",
    raw_quote_en: str = "",
) -> dict[str, Any]:
    return {
        "source": "youtube",
        "author": "@somepundit",
        "url": f"https://www.youtube.com/watch?v={video_id}&t={int(ts)}s",
        "ts": ts,
        "horizon": "swing",
        "confidence": "",
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
        "raw_quote_en": raw_quote_en,
    }


# Deliberately NOT the same field values. Pass 2 extracts each leg from the moment it
# was spoken, so the entry leg carries no target and the target leg carries no stop —
# the shared entry price is the whole of the evidence that they are one position.
_ENTRY_LEG = _call_row(
    ts=162.0,
    target="",
    raw_quote_en="we are long from 115,200 with the stop under 113,800",
)
_TARGET_LEG = _call_row(
    ts=886.0,
    stop="",
    raw_quote_en="the first target for this long sits at 118,500",
)
_UNRELATED_CALL = _call_row(
    ts=1500.0,
    symbol="ETHUSDT",
    direction="short",
    entry="3620",
    stop="3705",
    target="3410",
    raw_quote_en="ether looks heavy into the 3,705 supply shelf",
)

# The real umX9m7y7jsU pair, which route_dedup's own key docstring names as the rows
# this module exists to PROTECT: same video, same symbol, same direction, two
# genuinely different trades. Same-source matching must not collapse them.
_SWEEP_BUY = _call_row(
    ts=162.6,
    stop="",
    target="",
    entry="conditional long-term spot buy on a sweep below the 57,856.93 week low",
    raw_quote_en="if we sweep the weekly low I am bidding spot down there",
)
_BREAKOUT_LONG = _call_row(
    ts=886.2,
    stop="",
    entry="breakout long on confirmed acceptance above 80,000",
    target="110000",
    raw_quote_en="reclaiming eighty thousand opens the path to six figures next year",
)


def _jsonl(*rows: dict[str, Any]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def test_stream_c_stays_exempt_without_a_source_id() -> None:
    """Back-compat: no source id means no same-source scope, so nothing is checked."""
    assert find_similar("69k cost basis holds", PUNDIT_SINK, _jsonl(_ENTRY_LEG)) == []


def test_an_unrecognized_sink_is_quietly_unscopeable_not_an_error() -> None:
    """Scoping needs entries that persist a URL. A sink that has none — including a
    typo'd `--sink` — returns [] as it always did, rather than raising out of the
    entry splitter."""
    assert find_similar("anything", "docs/plans/typo.md", "", source_id=_VIDEO_ID) == []
    assert semantic_scope("docs/plans/typo.md", _VIDEO_ID) == "none"


def test_stream_c_flags_a_same_source_restatement() -> None:
    hits = find_similar(
        "long 115,200 targeting 118,500",
        PUNDIT_SINK,
        _jsonl(_ENTRY_LEG),
        source_id=_VIDEO_ID,
    )
    assert hits
    assert 115200.0 in hits[0].shared_levels


def test_stream_c_ignores_an_identical_call_from_a_different_source() -> None:
    """The cross-author exemption, unchanged: this is the case that must NOT fire."""
    other = _call_row(video_id=_OTHER_VIDEO_ID, ts=40.0, raw_quote_en="long 115,200")
    assert (
        find_similar(
            "long 115,200 targeting 118,500",
            PUNDIT_SINK,
            _jsonl(other),
            source_id=_VIDEO_ID,
        )
        == []
    )


def test_stream_c_scoring_ignores_schema_keys() -> None:
    """Two unrelated calls from ONE video must not match on shared JSON keys.

    Scoring the raw line makes every Stream C pair look alike — `source`, `author`,
    `symbol`, `direction`, `horizon`, `confidence` are terms in every row — which is
    why the sink read as uniformly self-similar in the original calibration pass and
    the semantic layer was switched off for it wholesale.
    """
    assert (
        find_similar(
            "ether short into the 3,705 supply shelf",
            PUNDIT_SINK,
            _jsonl(_ENTRY_LEG),
            source_id=_VIDEO_ID,
        )
        == []
    )


# ---------------------------------------------------------------------------
# Intra-batch pairs. Every check in the review digest runs BEFORE approval, so when
# a video's items are checked none of them are on disk yet — the pair that shipped
# the defect is invisible to any sink-file comparison. It is only findable item-vs-item.
# ---------------------------------------------------------------------------


def test_two_legs_of_one_position_are_flagged() -> None:
    pairs = find_source_duplicate_pairs([_ENTRY_LEG, _TARGET_LEG])
    assert len(pairs) == 1
    assert (pairs[0].left_ts, pairs[0].right_ts) == (162.0, 886.0)
    assert 115200.0 in pairs[0].shared_levels


def test_genuinely_different_calls_in_one_video_are_not_flagged() -> None:
    assert find_source_duplicate_pairs([_ENTRY_LEG, _UNRELATED_CALL]) == []


def test_two_distinct_trades_on_one_symbol_survive() -> None:
    """The adversarial negative: matching symbol AND direction, different trade."""
    assert find_source_duplicate_pairs([_SWEEP_BUY, _BREAKOUT_LONG]) == []


def test_pairs_needs_at_least_two_items() -> None:
    assert find_source_duplicate_pairs([]) == []
    assert find_source_duplicate_pairs([_ENTRY_LEG]) == []


def test_pairs_are_reported_once_not_in_both_orders() -> None:
    pairs = find_source_duplicate_pairs([_ENTRY_LEG, _TARGET_LEG, _UNRELATED_CALL])
    assert len(pairs) == 1


def test_pairs_cli_reads_an_items_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    items = tmp_path / "items.json"
    items.write_text(json.dumps([_ENTRY_LEG, _TARGET_LEG]), encoding="utf-8")
    main(["pairs", "--items", str(items)])
    out = json.loads(capsys.readouterr().out)
    assert len(out["pairs"]) == 1
    assert out["pairs"][0]["left_ts"] == 162.0


def test_check_reports_the_semantic_scope_it_actually_used(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`semantic_checked` alone can no longer describe Stream C — it is checked, but
    only against its own source. The digest has to be able to say which."""
    sink = tmp_path / "pundit-calls.jsonl"
    sink.write_text(_jsonl(_ENTRY_LEG), encoding="utf-8")
    main(
        [
            "check",
            "--source-id",
            _VIDEO_ID,
            "--item-ts",
            "886",
            "--sink",
            PUNDIT_SINK,
            "--sink-path",
            str(sink),
            "--text",
            "long 115,200 targeting 118,500",
            "--ledger",
            str(tmp_path / "routed.json"),
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert out["semantic_checked"] is True
    assert out["semantic_scope"] == "same-source"
    assert out["candidates"]


def test_check_reports_full_scope_on_stream_a(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sink = tmp_path / "thesis-inbox.md"
    sink.write_text(_INBOX, encoding="utf-8")
    main(
        [
            "check",
            "--source-id",
            _VIDEO_ID,
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
    assert json.loads(capsys.readouterr().out)["semantic_scope"] == "all-entries"


# ---------------------------------------------------------------------------
# Seeding. Without this the identity layer ships blind to everything already
# routed — exactly the cached-post-re-routed case backlog #7 was written about.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://x.com/JordiCharts/status/2072488868553601519", "2072488868553601519"),
        ("https://twitter.com/a/status/123", "123"),
        ("https://www.youtube.com/watch?v=umX9m7y7jsU&t=886s", "umX9m7y7jsU"),
        ("https://youtu.be/0jctzIc5t_E?t=251s", "0jctzIc5t_E"),
    ],
)
def test_parse_source_id_reads_every_url_shape(url: str, expected: str) -> None:
    assert parse_source_id(url) == expected


# The F2 card dual-writes its own calls into Stream C; those are not ingested
# content and must not enter the routing ledger.
@pytest.mark.parametrize("url", ["ai-card://1783907021091-BTCUSDT", "", "nonsense"])
def test_parse_source_id_returns_none_for_non_ingested_urls(url: str) -> None:
    assert parse_source_id(url) is None


def test_seed_items_uses_the_rows_own_ts_so_it_matches_what_mark_would_write() -> None:
    rows = [{"url": "https://youtu.be/0jctzIc5t_E?t=251s", "ts": 251.0}]
    items = seed_items(rows, sink=PUNDIT_SINK, fallback_ts_utc="2026-07-31T00:00:00Z")
    assert items[0].source_id == "0jctzIc5t_E"
    assert items[0].item_ts == 251.0


def test_seed_items_defaults_an_x_post_to_zero_ts() -> None:
    rows = [{"url": "https://x.com/a/status/99"}]
    items = seed_items(rows, sink=PUNDIT_SINK, fallback_ts_utc="2026-07-31T00:00:00Z")
    assert items[0].item_ts == 0.0


def test_seed_items_skips_underivable_urls() -> None:
    rows = [{"url": "ai-card://x"}, {"url": "https://x.com/a/status/99"}]
    items = seed_items(rows, sink=PUNDIT_SINK, fallback_ts_utc="2026-07-31T00:00:00Z")
    assert len(items) == 1


def test_seed_items_prefers_the_rows_own_ingest_time() -> None:
    rows = [
        {"url": "https://x.com/a/status/99", "ingested_ts_utc": "2026-07-01T00:00:00Z"}
    ]
    items = seed_items(rows, sink=PUNDIT_SINK, fallback_ts_utc="2026-07-31T00:00:00Z")
    assert items[0].routed_ts_utc == "2026-07-01T00:00:00Z"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_seed_writes_nothing_without_apply(tmp_path: Path) -> None:
    sink = tmp_path / "pundit-calls.jsonl"
    sink.write_text('{"url":"https://x.com/a/status/99"}\n', encoding="utf-8")
    ledger = tmp_path / "routed.json"
    assert main(["seed", "--sink-path", str(sink), "--ledger", str(ledger)]) == 0
    assert not ledger.exists()


def test_seed_with_apply_populates_and_then_blocks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sink = tmp_path / "pundit-calls.jsonl"
    sink.write_text('{"url":"https://x.com/a/status/99"}\n', encoding="utf-8")
    ledger = tmp_path / "routed.json"
    main(["seed", "--sink-path", str(sink), "--ledger", str(ledger), "--apply"])
    capsys.readouterr()
    main(
        [
            "check",
            "--source-id",
            "99",
            "--item-ts",
            "0",
            "--sink",
            PUNDIT_SINK,
            "--sink-path",
            str(sink),
            "--text",
            "x",
            "--ledger",
            str(ledger),
        ]
    )
    assert json.loads(capsys.readouterr().out)["already_routed"] is True


# A count that ignores what's already in the ledger is exactly the kind of
# misleading number this module exists to stop.
def test_seed_dry_run_counts_only_what_is_actually_new(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sink = tmp_path / "pundit-calls.jsonl"
    sink.write_text(
        '{"url":"https://x.com/a/status/99"}\n{"url":"https://x.com/a/status/100"}\n',
        encoding="utf-8",
    )
    ledger = tmp_path / "routed.json"
    main(["seed", "--sink-path", str(sink), "--ledger", str(ledger), "--apply"])
    capsys.readouterr()
    main(["seed", "--sink-path", str(sink), "--ledger", str(ledger)])
    out = capsys.readouterr().out
    assert "0 new" in out
    assert "2 already" in out


def test_seed_is_idempotent(tmp_path: Path) -> None:
    sink = tmp_path / "pundit-calls.jsonl"
    sink.write_text('{"url":"https://x.com/a/status/99"}\n', encoding="utf-8")
    ledger = tmp_path / "routed.json"
    for _ in range(2):
        main(["seed", "--sink-path", str(sink), "--ledger", str(ledger), "--apply"])
    assert len(load_ledger(ledger)) == 1


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


def test_author_scope_is_reported_when_an_author_is_supplied() -> None:
    assert (
        semantic_scope("docs/plans/pundit-calls.jsonl", "abc123", author="Traderfengge")
        == "same-author"
    )


def test_source_scope_is_unchanged_when_no_author_is_supplied() -> None:
    assert semantic_scope("docs/plans/pundit-calls.jsonl", "abc123") == "same-source"


def test_same_author_across_two_sources_is_comparable() -> None:
    """One author's one call, arriving twice: once first-hand, once relayed."""
    sink_text = (
        '{"url": "https://youtu.be/AAA", "author": "Traderfengge", '
        '"symbol": "BTCUSDT", "direction": "long", "entry": "64000"}\n'
        '{"url": "https://youtu.be/BBB", "author": "someone_else", '
        '"symbol": "BTCUSDT", "direction": "long", "entry": "64000"}\n'
    )
    got = _comparable_entries(
        "docs/plans/pundit-calls.jsonl", sink_text, "CCC", author="Traderfengge"
    )
    assert len(got) == 1
    assert "64000" in got[0][0]


def test_different_authors_are_NOT_compared_so_the_exemption_survives() -> None:
    """Two pundits agreeing is two observations, not a duplicate. This is the test
    that fails if author scoping is applied too broadly."""
    sink_text = (
        '{"url": "https://youtu.be/AAA", "author": "someone_else", '
        '"symbol": "BTCUSDT", "direction": "long", "entry": "64000"}\n'
    )
    got = _comparable_entries(
        "docs/plans/pundit-calls.jsonl", sink_text, "CCC", author="Traderfengge"
    )
    assert got == []


def test_author_matching_normalises_the_at_sign_on_both_sides() -> None:
    sink_text = (
        '{"url": "https://youtu.be/AAA", "author": "@Traderfengge", '
        '"symbol": "BTCUSDT", "direction": "long", "entry": "64000"}\n'
    )
    got = _comparable_entries(
        "docs/plans/pundit-calls.jsonl", sink_text, "CCC", author="Traderfengge"
    )
    assert len(got) == 1


# ---------------------------------------------------------------------------
# ST99 — `--sink` is an allowlist, and ST101 — the tool runs bare
# ---------------------------------------------------------------------------


class TestSinkIsValidatedAtTheCLIBoundary:
    """`--sink` used to accept any string, and all 30 bad ledger rows went in that way.

    `is_routed` keys on `(source_id, item_ts, sink)`, so a sink outside `KNOWN_SINKS`
    is dedup-BLIND — the row is written and can never be found again. That includes 8
    YouTube ids, so the video half of the round was blind too.
    """

    @staticmethod
    def _args(sink: str, ledger: Path) -> list[str]:
        return [
            "mark",
            "--source-id",
            "1234567890",
            "--item-ts",
            "0",
            "--sink",
            sink,
            "--ledger",
            str(ledger),
        ]

    def test_a_bare_filename_is_rejected(self, tmp_path: Path) -> None:
        """26 of the 30 bad rows were exactly this: `mechanics-backlog.md`."""
        with pytest.raises(SystemExit) as exc:
            main(self._args("mechanics-backlog.md", tmp_path / "l.json"))
        assert exc.value.code == 2

    def test_an_empty_sink_is_rejected(self, tmp_path: Path) -> None:
        """The other 4: id and sink joined into one `--source-id`, leaving `--sink` empty."""
        with pytest.raises(SystemExit) as exc:
            main(self._args("", tmp_path / "l.json"))
        assert exc.value.code == 2

    def test_a_rejected_sink_writes_NOTHING(self, tmp_path: Path) -> None:
        """The point of the gate: refuse before the ledger is touched, never after."""
        ledger = tmp_path / "l.json"
        with pytest.raises(SystemExit):
            main(self._args("mechanics-backlog.md", ledger))
        assert not ledger.exists()

    @pytest.mark.parametrize("sink", [THESIS_SINK, MECHANICS_SINK, PUNDIT_SINK])
    def test_every_real_sink_is_accepted(self, sink: str, tmp_path: Path) -> None:
        ledger = tmp_path / "l.json"
        assert main(self._args(sink, ledger)) == 0
        assert is_routed(load_ledger(ledger), "1234567890", 0.0, sink)

    def test_the_allowlist_is_exactly_what_x_route_can_return(self) -> None:
        """Drift guard. `KNOWN_SINKS` is the CLI's gate and `route_target` is what
        produces the value passed to it; if they ever disagree the gate rejects a
        legitimate route."""
        produced = {
            route_target("setup", ""),
            route_target("mechanic", ""),
            route_target("claim", "NOVEL"),
        }
        assert produced == set(KNOWN_SINKS)

    def test_the_LIBRARY_stays_lenient_so_the_gate_has_exactly_one_home(self) -> None:
        """Scoped, not blanket. `find_similar` must keep returning [] on an unknown
        sink — an unscopeable sink is genuinely not an error there, and duplicating
        the check into the library is how a second spelling starts."""
        assert find_similar("anything", "docs/plans/typo.md", "") == []
        assert semantic_scope("docs/plans/typo.md", "vid1") == "none"

    def test_route_reconcile_shares_the_ONE_definition(self) -> None:
        """It restated the tuple locally until ST99. Two spellings that agree by
        coincidence read exactly like two that agree by construction."""
        from tools import route_reconcile

        assert route_reconcile.KNOWN_SINKS is KNOWN_SINKS


def test_bare_invocation_works(tmp_path: Path) -> None:
    """`python3 tools/route_dedup.py` must run without PYTHONPATH=.

    It imports `analytics.pundit_authors`, so a bare run put `tools/` on sys.path and
    died on ModuleNotFoundError — the `distil_power.py` shape. Per ST101 the guarantee
    is THIS test, not the bootstrap line.
    """
    ledger = tmp_path / "l.json"
    proc = subprocess.run(
        [
            sys.executable,
            "tools/route_dedup.py",
            "mark",
            "--source-id",
            "1234567890",
            "--item-ts",
            "0",
            "--sink",
            PUNDIT_SINK,
            "--ledger",
            str(ledger),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
    )
    assert proc.returncode == 0, proc.stderr
    assert ledger.exists()
