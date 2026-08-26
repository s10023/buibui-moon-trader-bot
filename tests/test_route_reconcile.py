"""Tests for tools/route_reconcile.py — the ST100 post-round route reconciliation.

The defect this guards: on the 2026-08-25 X round, 19 Stream C setups were DECLARED
routed in their notes and never written to `pundit-calls.jsonl`. Streams A and B landed;
Stream C's append simply never ran. Nothing reconciled the declaration against the sink,
so it went unnoticed for two days and the notes were the only copy.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.route_dedup import MECHANICS_SINK, PUNDIT_SINK, THESIS_SINK, RoutedItem
from tools.route_reconcile import (
    Declaration,
    parse_declaration,
    reconcile,
    render,
    summarise,
)
from tools.route_reconcile import main as reconcile_main

REPO = Path(__file__).resolve().parent.parent

# A 19-digit X status id and an 11-char YouTube id — the two real shapes.
STATUS_ID = "2090703752311533931"
VIDEO_ID = "yC0_4PPuDPk"


def _note(**kw: str) -> str:
    """An x-note's frontmatter, in the real on-disk shape."""
    fields = {
        "status_id": STATUS_ID,
        "author": "sergio_tesla_",
        "source": "twitter",
        "url": f"https://x.com/sergio_tesla_/status/{STATUS_ID}",
        "ingested": "2026-08-26",
        "route": PUNDIT_SINK,
        **kw,
    }
    body = "\n".join(f"{k}: {v}" for k, v in fields.items() if v != "\x00")
    return f"---\n{body}\n---\n\n# a note\n"


def _c_row(source_id: str) -> str:
    """One real Stream C line — attribution there is the row's OWN url, never a
    substring, so a fixture has to be well-formed JSONL to count as landed."""
    return json.dumps(
        {"url": f"https://x.com/a/status/{source_id}", "symbol": "BTCUSDT"}
    )


def _mark(source_id: str, sink: str, item_ts: float = 0.0) -> RoutedItem:
    return RoutedItem(
        source_id=source_id,
        item_ts=item_ts,
        sink=sink,
        routed_ts_utc="2026-08-26T03:34:14+00:00",
    )


def _decl(**kw: object) -> Declaration:
    base: dict[str, object] = {
        "note": "2026-08-26-sergio-tesla.md",
        "source_id": STATUS_ID,
        "route": PUNDIT_SINK,
    }
    return Declaration(**{**base, **kw})  # type: ignore[arg-type]


class TestParseDeclaration:
    def test_reads_route_and_status_id_from_frontmatter(self) -> None:
        d = parse_declaration("n.md", _note())
        assert d.source_id == STATUS_ID
        assert d.route == PUNDIT_SINK

    def test_route_is_empty_when_the_key_is_absent(self) -> None:
        """Every one of the 135 video-notes is this shape — no `route:` key at all.

        It must read as UNDECLARED rather than as a clean note, or pointing the tool at
        `/ingest-video`'s corpus would report a spurious all-clear.
        """
        d = parse_declaration("v.md", _note(route="\x00"))
        assert d.route == ""

    def test_derives_source_id_from_video_id(self) -> None:
        text = _note(status_id="\x00", video_id=VIDEO_ID)
        assert parse_declaration("v.md", text).source_id == VIDEO_ID

    def test_falls_back_to_the_url_when_no_id_field_is_present(self) -> None:
        text = _note(status_id="\x00")
        assert parse_declaration("n.md", text).source_id == STATUS_ID

    def test_dropped_is_carried_through_verbatim(self) -> None:
        assert parse_declaration("n.md", _note(route="dropped")).route == "dropped"


class TestParseProseRoutingDecision:
    """The 2026-08-26 round records routing in a `## Routing decision` section instead
    of a frontmatter key — 11 of its 18 notes. A frontmatter-only reader calls all 11
    `undeclared`, which is a false alarm on a round that was in fact routed correctly.
    """

    def _prose(self, body: str) -> str:
        front = _note(route="\x00")
        return f"{front}\n## Routing decision\n\n{body}\n"

    def test_reads_a_routed_arrow_line(self) -> None:
        d = parse_declaration(
            "n.md",
            self._prose(
                f"ROUTED -> {THESIS_SINK} (claim/NOVEL). route_dedup mark recorded, "
                f"source-id {STATUS_ID}."
            ),
        )
        assert d.route == THESIS_SINK

    def test_reads_a_dropped_line(self) -> None:
        body = "DROPPED - route_target(claim, ALREADY-TESTED) -> None. Nothing written."
        assert parse_declaration("n.md", self._prose(body)).route == "dropped"

    def test_reads_dropped_before_dispatch(self) -> None:
        body = "DROPPED BEFORE DISPATCH - never extracted, no subagent spent."
        assert parse_declaration("n.md", self._prose(body)).route == "dropped"

    def test_not_written_is_a_drop_even_though_it_names_a_sink(self) -> None:
        """The real trap, verbatim from `2026-08-26-luckychartape-2072102057767735427`.

        The line names `pundit-calls.jsonl` as what `route_target` WOULD return, then
        says the operator dropped it. Deciding on the path rather than the leading
        verdict would turn every operator override into a spurious `unperformed`.
        """
        body = (
            f"NOT WRITTEN. route_target(setup, NOVEL) returns {PUNDIT_SINK}, but the "
            "operator approved dropping it. No mark recorded, so it stays re-routable."
        )
        assert parse_declaration("n.md", self._prose(body)).route == "dropped"

    def test_frontmatter_wins_over_prose(self) -> None:
        text = _note(route=MECHANICS_SINK) + "\n## Routing decision\n\nDROPPED - x.\n"
        assert parse_declaration("n.md", text).route == MECHANICS_SINK

    def test_an_unrecognised_verdict_stays_undeclared(self) -> None:
        """Silence is not a drop. A new prose form must surface as `undeclared` so it
        gets read by a human, never be guessed at."""
        assert parse_declaration("n.md", self._prose("Still deciding.")).route == ""


class TestReconcile:
    def test_ok_when_the_row_landed_and_the_ledger_was_marked(self) -> None:
        findings = reconcile(
            [_decl()],
            sink_texts={
                PUNDIT_SINK: f'{{"url": "https://x.com/a/status/{STATUS_ID}"}}'
            },
            ledger=[_mark(STATUS_ID, PUNDIT_SINK)],
        )
        assert [f.verdict for f in findings] == ["ok"]

    def test_unperformed_when_declared_but_the_sink_never_got_the_row(self) -> None:
        """The 2026-08-25 defect, reproduced: note says routed, sink is empty."""
        findings = reconcile(
            [_decl()],
            sink_texts={PUNDIT_SINK: ""},
            ledger=[],
        )
        assert [f.verdict for f in findings] == ["unperformed"]

    def test_unperformed_even_when_the_ledger_was_marked(self) -> None:
        """A mark is a second declaration, not evidence of a write.

        `route_dedup seed` reconstructs FROM the sink, so a marked-but-unwritten row is
        the one state nothing downstream can repair.
        """
        findings = reconcile(
            [_decl()],
            sink_texts={PUNDIT_SINK: ""},
            ledger=[_mark(STATUS_ID, PUNDIT_SINK)],
        )
        assert [f.verdict for f in findings] == ["unperformed"]

    def test_unmarked_when_the_row_landed_but_the_ledger_did_not(self) -> None:
        findings = reconcile(
            [_decl()],
            sink_texts={PUNDIT_SINK: _c_row(STATUS_ID)},
            ledger=[],
        )
        assert [f.verdict for f in findings] == ["unmarked"]

    def test_a_mark_on_a_different_sink_does_not_count(self) -> None:
        findings = reconcile(
            [_decl()],
            sink_texts={PUNDIT_SINK: _c_row(STATUS_ID)},
            ledger=[_mark(STATUS_ID, MECHANICS_SINK)],
        )
        assert [f.verdict for f in findings] == ["unmarked"]

    def test_dropped_is_clean_when_nothing_landed(self) -> None:
        findings = reconcile(
            [_decl(route="dropped")],
            sink_texts={PUNDIT_SINK: "", MECHANICS_SINK: ""},
            ledger=[],
        )
        assert [f.verdict for f in findings] == ["dropped"]

    def test_phantom_when_a_dropped_item_is_a_real_stream_c_row(self) -> None:
        """The reverse defect — a drop is a decision, and a row that appears despite it
        is unreviewed content in the sink. Exact, because Stream C rows carry their own
        `url`."""
        findings = reconcile(
            [_decl(route="dropped")],
            sink_texts={
                PUNDIT_SINK: f'{{"url": "https://x.com/a/status/{STATUS_ID}"}}',
                MECHANICS_SINK: "",
            },
            ledger=[],
        )
        assert findings[0].verdict == "phantom"
        assert PUNDIT_SINK in findings[0].detail

    def test_a_dropped_item_merely_CITED_in_a_prose_sink_is_not_phantom(self) -> None:
        """Measured on the real 08-26 round: 4 of 4 `phantom` hits were corroboration
        lines appended to an EXISTING entry — one says so verbatim, "Routed as a
        corroboration line rather than a new bullet."

        Streams A and B persist no per-entry source field, so an id inside an entry may
        be its origin OR a predecessor/successor/corroboration citation. Calling that a
        defect makes the tool cry wolf on a normal round, which is how a red line teaches
        its reader to skip it.
        """
        findings = reconcile(
            [_decl(route="dropped")],
            sink_texts={PUNDIT_SINK: "", MECHANICS_SINK: f"- ...{STATUS_ID}..."},
            ledger=[],
        )
        assert findings[0].verdict == "cited"
        assert MECHANICS_SINK in findings[0].detail

    def test_stream_c_attribution_is_the_rows_own_url_not_a_substring(self) -> None:
        """A row QUOTING another post's id must not count as that post being routed."""
        row = f'{{"url": "https://x.com/a/status/111", "raw_quote": "see {STATUS_ID}"}}'
        findings = reconcile(
            [_decl()],
            sink_texts={PUNDIT_SINK: row},
            ledger=[_mark(STATUS_ID, PUNDIT_SINK)],
        )
        assert [f.verdict for f in findings] == ["unperformed"]

    def test_undeclared_when_the_note_names_no_route(self) -> None:
        findings = reconcile([_decl(route="")], sink_texts={}, ledger=[])
        assert [f.verdict for f in findings] == ["undeclared"]

    def test_unknown_sink_is_not_silently_treated_as_a_drop(self) -> None:
        """ST99's bare-filename bug wrote 26 rows against `mechanics-backlog.md`.

        A route naming something that is not one of the three sinks must be a finding,
        never a pass.
        """
        findings = reconcile(
            [_decl(route="mechanics-backlog.md")], sink_texts={}, ledger=[]
        )
        assert [f.verdict for f in findings] == ["unknown-sink"]

    def test_a_bare_filename_mark_is_named_as_malformed_not_just_missing(self) -> None:
        """ST99 wrote 26 rows with a bare filename instead of the full sink path.

        Those never match, so the row reads `unmarked` — identical to never having been
        marked at all. The repair differs (fix the row vs. write one), so the output has
        to separate them.
        """
        findings = reconcile(
            [_decl()],
            sink_texts={PUNDIT_SINK: _c_row(STATUS_ID)},
            ledger=[_mark(STATUS_ID, "pundit-calls.jsonl")],
        )
        assert findings[0].verdict == "unmarked"
        assert "malformed" in findings[0].detail
        assert "pundit-calls.jsonl" in findings[0].detail

    def test_an_id_that_swallowed_the_sink_is_named_as_malformed(self) -> None:
        """The other ST99 shape: 4 rows had id and sink joined into one --source-id,
        leaving the sink empty."""
        findings = reconcile(
            [_decl()],
            sink_texts={PUNDIT_SINK: _c_row(STATUS_ID)},
            ledger=[_mark(f"{STATUS_ID} {PUNDIT_SINK}", "")],
        )
        assert findings[0].verdict == "unmarked"
        assert "malformed" in findings[0].detail

    def test_a_clean_miss_does_not_claim_a_malformed_mark(self) -> None:
        findings = reconcile(
            [_decl()], sink_texts={PUNDIT_SINK: _c_row(STATUS_ID)}, ledger=[]
        )
        assert findings[0].verdict == "unmarked"
        assert "malformed" not in findings[0].detail

    def test_a_multi_sink_route_needs_every_sink_to_have_landed(self) -> None:
        """One video legitimately yields several items under one id, so `/ingest-video`
        notes list every sink they touched. Checking only the first would let the other
        half go missing silently — the exact 08-25 shape."""
        findings = reconcile(
            [_decl(route=f"{PUNDIT_SINK}, {MECHANICS_SINK}")],
            sink_texts={PUNDIT_SINK: _c_row(STATUS_ID), MECHANICS_SINK: ""},
            ledger=[_mark(STATUS_ID, PUNDIT_SINK)],
        )
        assert findings[0].verdict == "unperformed"
        assert MECHANICS_SINK in findings[0].detail

    def test_a_multi_sink_route_is_ok_when_all_of_them_landed(self) -> None:
        findings = reconcile(
            [_decl(route=f"{PUNDIT_SINK}, {MECHANICS_SINK}")],
            sink_texts={
                PUNDIT_SINK: _c_row(STATUS_ID),
                MECHANICS_SINK: f"- ...{STATUS_ID}...",
            },
            ledger=[_mark(STATUS_ID, PUNDIT_SINK), _mark(STATUS_ID, MECHANICS_SINK)],
        )
        assert findings[0].verdict == "ok"

    def test_a_trailing_comma_in_a_prose_route_does_not_break_the_sink_name(
        self,
    ) -> None:
        text = (
            _note(route="\x00")
            + f"\n## Routing decision\n\nROUTED -> {THESIS_SINK}, plus a note.\n"
        )
        d = parse_declaration("n.md", text)
        findings = reconcile(
            [d],
            sink_texts={THESIS_SINK: f"## ...{STATUS_ID}..."},
            ledger=[_mark(STATUS_ID, THESIS_SINK)],
        )
        assert findings[0].verdict == "ok"

    def test_each_note_is_reported_once_in_input_order(self) -> None:
        decls = [
            _decl(note="a.md", source_id="1"),
            _decl(note="b.md", source_id="2"),
        ]
        findings = reconcile(decls, sink_texts={PUNDIT_SINK: _c_row("1")}, ledger=[])
        assert [f.note for f in findings] == ["a.md", "b.md"]
        assert [f.verdict for f in findings] == ["unmarked", "unperformed"]


class TestSummarise:
    def test_counts_every_verdict_it_saw(self) -> None:
        findings = reconcile(
            [
                _decl(note="a.md", source_id="1"),
                _decl(note="b.md", source_id="2"),
                _decl(note="c.md", source_id="3", route="dropped"),
            ],
            sink_texts={PUNDIT_SINK: _c_row("1")},
            ledger=[_mark("1", PUNDIT_SINK)],
        )
        text = summarise(findings)
        assert "3 note(s)" in text
        assert "1 ok" in text
        assert "1 unperformed" in text

    def test_render_names_the_sink_a_row_failed_to_reach(self) -> None:
        findings = reconcile([_decl()], sink_texts={PUNDIT_SINK: ""}, ledger=[])
        assert PUNDIT_SINK in render(findings)

    def test_render_is_explicit_when_everything_reconciles(self) -> None:
        findings = reconcile(
            [_decl(route="dropped")], sink_texts={PUNDIT_SINK: ""}, ledger=[]
        )
        assert "reconciled" in render(findings).lower()


class TestCli:
    def _round(self, tmp_path: Path, *, note_text: str, pundit: str = "") -> list[str]:
        notes = tmp_path / "notes"
        notes.mkdir()
        (notes / "one.md").write_text(note_text, encoding="utf-8")
        (tmp_path / "pundit.jsonl").write_text(pundit, encoding="utf-8")
        (tmp_path / "thesis.md").write_text("", encoding="utf-8")
        (tmp_path / "mech.md").write_text("", encoding="utf-8")
        (tmp_path / "ledger.json").write_text(
            json.dumps({"version": 1, "items": []}), encoding="utf-8"
        )
        return [
            str(notes / "one.md"),
            "--ledger",
            str(tmp_path / "ledger.json"),
            "--pundit-sink",
            str(tmp_path / "pundit.jsonl"),
            "--thesis-sink",
            str(tmp_path / "thesis.md"),
            "--mechanics-sink",
            str(tmp_path / "mech.md"),
        ]

    def test_exit_1_when_a_declared_route_was_never_performed(
        self, tmp_path: Path
    ) -> None:
        argv = self._round(tmp_path, note_text=_note())
        assert reconcile_main(argv) == 1

    def test_exit_0_when_every_declaration_reconciles(self, tmp_path: Path) -> None:
        argv = self._round(tmp_path, note_text=_note(route="dropped"))
        assert reconcile_main(argv) == 0

    def test_json_mode_emits_the_findings(self, tmp_path: Path, capsys: object) -> None:
        argv = [*self._round(tmp_path, note_text=_note()), "--json"]
        reconcile_main(argv)
        out = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
        assert out["findings"][0]["verdict"] == "unperformed"

    def test_bare_invocation_works(self, tmp_path: Path) -> None:
        """`python3 tools/route_reconcile.py` must run without PYTHONPATH.

        This tool DOES import from the repo (`tools.route_dedup`), so unlike
        `x_truncated.py` it needs a sys.path bootstrap — and per ST101 the guarantee is
        this test, not the bootstrap line itself.
        """
        note = tmp_path / "one.md"
        note.write_text(_note(route="dropped"), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "tools/route_reconcile.py", str(note)],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
