"""Tests for `tools/docs_index.py` — the generated audit + spec indexes.

The load-bearing tests here are the *negative* ones. This corpus keeps table
rows, blockquotes and `**Date:**` metadata lines directly under Verdict
headings, and every one of them would render as a plausible-looking but wrong
verdict, so `verdict_from_markdown` returning None is the behaviour worth
guarding. Likewise the reconcile fixture carries three distinguishable specs —
reconciled, referenced-but-not-reconciled, and unreferenced — so a rule that
simply returned True (or the reference list) would fail rather than pass.
"""

from __future__ import annotations

from pathlib import Path

from tools.docs_index import (
    INDEX_NAME,
    build_indexes,
    collect_audits,
    collect_specs,
    date_from_filename,
    main,
    render_audit_index,
    title_from_markdown,
    verdict_from_markdown,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestDateFromFilename:
    def test_reads_the_iso_prefix(self) -> None:
        assert date_from_filename("2026-05-17-adr-exempt.md") == "2026-05-17"

    def test_returns_none_without_a_prefix(self) -> None:
        assert date_from_filename("INDEX.md") is None
        assert date_from_filename("notes.md") is None


class TestTitleFromMarkdown:
    def test_reads_the_first_h1(self) -> None:
        assert (
            title_from_markdown("# H14 — Coinbase premium\n\nbody\n")
            == "H14 — Coinbase premium"
        )

    def test_strips_a_trailing_date_parenthetical(self) -> None:
        text = "# P3 Carry sleeve — funding-carry G-gate verdict (2026-06-19)\n"
        assert title_from_markdown(text) == "P3 Carry sleeve — funding-carry G-gate"

    def test_ignores_a_parenthetical_that_is_not_a_date(self) -> None:
        assert title_from_markdown("# Exit-policy A/B v1 (read-only replay)\n") == (
            "Exit-policy A/B v1 (read-only replay)"
        )

    def test_returns_empty_when_there_is_no_h1(self) -> None:
        assert title_from_markdown("## only a subheading\n") == ""


class TestVerdictFromMarkdown:
    def test_reads_the_inline_bold_form(self) -> None:
        text = "# Carry\n\n**Verdict: FAIL the de-biased gate. No standalone edge in funding carry.**\n"
        verdict = verdict_from_markdown(text)
        assert verdict is not None
        assert verdict.startswith("FAIL the de-biased gate")

    def test_reads_prose_under_a_verdict_heading(self) -> None:
        text = (
            "# Reference-level proximity\n\n"
            "## Verdict\n\n"
            "Live near-level cohort does not clear the de-biased gate — do not build.\n"
        )
        assert verdict_from_markdown(text) == (
            "Live near-level cohort does not clear the de-biased gate — do not build."
        )

    def test_rejects_a_table_row(self) -> None:
        text = "# ST9\n\n## Verdict\n\n| Strategy | TF | Decision | n | baseline avg_r | lift |\n"
        assert verdict_from_markdown(text) is None

    def test_rejects_a_blockquote(self) -> None:
        text = "# P2\n\n## Verdict\n\n> Read-only audit. Engine: `analytics/forecast/`. Driver follows.\n"
        assert verdict_from_markdown(text) is None

    def test_rejects_a_metadata_line(self) -> None:
        text = "# P3 xsmom\n\n## Verdict\n\n**Date:** 2026-06-16 · **Status:** published result\n"
        assert verdict_from_markdown(text) is None

    def test_rejects_a_numbered_list_item(self) -> None:
        text = "# MFE/MAE\n\n## Verdict\n\n1. **Expired often reaches 1R** → CONFIRMED, dominant\n"
        assert verdict_from_markdown(text) is None

    def test_rejects_a_fragment_below_the_length_floor(self) -> None:
        assert verdict_from_markdown("# Doc\n\n## Verdict\n\nNo.\n") is None

    def test_returns_none_when_there_is_no_verdict_section(self) -> None:
        text = "# T6 Phase A — adr_exempt Audit Findings\n\n## Method\n\nSome prose about method.\n"
        assert verdict_from_markdown(text) is None

    def test_stops_at_the_next_heading(self) -> None:
        text = (
            "# Doc\n\n## Verdict\n\n| a | b |\n\n"
            "## Method\n\nThis prose sits under Method and must not be read as the verdict.\n"
        )
        assert verdict_from_markdown(text) is None


class TestCollectAudits:
    def test_skips_the_index_and_undated_files_and_sorts_newest_first(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "2026-01-02-old.md").write_text(
            "# Old audit doc\n", encoding="utf-8"
        )
        (tmp_path / "2026-03-04-new.md").write_text(
            "# New audit doc\n", encoding="utf-8"
        )
        (tmp_path / INDEX_NAME).write_text("# Audit index\n", encoding="utf-8")
        (tmp_path / "scratch.md").write_text("# Undated\n", encoding="utf-8")

        rows = collect_audits(tmp_path)

        assert [r.filename for r in rows] == ["2026-03-04-new.md", "2026-01-02-old.md"]
        assert rows[0].title == "New audit doc"


class TestCollectSpecs:
    """Three distinguishable specs — a rule that returned True for all would fail."""

    def _corpus(self, tmp_path: Path) -> tuple[Path, Path]:
        specs = tmp_path / "specs"
        audits = tmp_path / "audits"
        specs.mkdir()
        audits.mkdir()

        for name in (
            "2026-06-15-reconciled-design.md",
            "2026-06-16-cited-design.md",
            "2026-06-17-orphan-design.md",
            "2026-06-18-f8-gate-design.md",
        ):
            (specs / name).write_text(f"# {name}\n", encoding="utf-8")

        (audits / "2026-08-06-spec-reconcile-run.md").write_text(
            "# Spec-vs-code reconcile\n\nWalked 2026-06-15-reconciled-design.md leg by leg.\n",
            encoding="utf-8",
        )
        (audits / "2026-07-01-plain-audit.md").write_text(
            "# A plain audit\n\nBackground reading: 2026-06-16-cited-design.md.\n",
            encoding="utf-8",
        )
        # The real false positive that the first rule produced: an audit that
        # reconciles two FINDINGS, under a heading in its body, while citing a spec.
        (audits / "2026-06-03-direction-axis-hard-flip.md").write_text(
            "# Direction-axis hard-flip decision doc\n\n"
            "Gate spec: 2026-06-18-f8-gate-design.md.\n\n"
            "## Reconciliation — the asymmetry is regime-contingent, not permanent\n\n"
            "Reconciling the two results rather than the spec against its code.\n",
            encoding="utf-8",
        )
        return specs, audits

    def test_reconciled_spec_is_detected(self, tmp_path: Path) -> None:
        specs, audits = self._corpus(tmp_path)
        rows = {r.filename: r for r in collect_specs(specs, audits)}
        assert rows["2026-06-15-reconciled-design.md"].reconciled_by == (
            "2026-08-06-spec-reconcile-run.md",
        )

    def test_a_plain_reference_is_not_a_reconcile(self, tmp_path: Path) -> None:
        specs, audits = self._corpus(tmp_path)
        rows = {r.filename: r for r in collect_specs(specs, audits)}
        cited = rows["2026-06-16-cited-design.md"]
        assert cited.referenced_by == ("2026-07-01-plain-audit.md",)
        assert cited.reconciled_by == ()

    def test_an_unreferenced_spec_is_neither(self, tmp_path: Path) -> None:
        specs, audits = self._corpus(tmp_path)
        rows = {r.filename: r for r in collect_specs(specs, audits)}
        orphan = rows["2026-06-17-orphan-design.md"]
        assert orphan.referenced_by == ()
        assert orphan.reconciled_by == ()

    def test_a_findings_reconciliation_section_is_not_a_spec_reconcile(
        self, tmp_path: Path
    ) -> None:
        """Regression: the first rule keyed on 'reconcil' anywhere in the body.

        It mislabelled `2026-06-03-direction-axis-hard-flip.md`, whose body has a
        `## Reconciliation` section about two findings, as reconciling the F8 gate
        spec. Reconcile status must key on the audit's own title.
        """
        specs, audits = self._corpus(tmp_path)
        rows = {r.filename: r for r in collect_specs(specs, audits)}
        gate = rows["2026-06-18-f8-gate-design.md"]
        assert gate.referenced_by == ("2026-06-03-direction-axis-hard-flip.md",)
        assert gate.reconciled_by == ()


class TestRender:
    def test_escapes_a_pipe_in_a_title(self, tmp_path: Path) -> None:
        (tmp_path / "2026-01-01-piped.md").write_text(
            "# A | B split\n", encoding="utf-8"
        )
        rendered = render_audit_index(collect_audits(tmp_path))
        assert "A \\| B split" in rendered

    def test_states_verdict_coverage_rather_than_implying_completeness(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "2026-01-01-a.md").write_text(
            "# With verdict\n\n## Verdict\n\nThis one states its verdict as a readable sentence.\n",
            encoding="utf-8",
        )
        (tmp_path / "2026-01-02-b.md").write_text(
            "# Without verdict\n", encoding="utf-8"
        )
        rendered = render_audit_index(collect_audits(tmp_path))
        assert "**1 of 2**" in rendered


class TestMainCheckMode:
    def test_check_fails_when_missing_then_passes_after_a_write(
        self, tmp_path: Path
    ) -> None:
        specs = tmp_path / "specs"
        audits = tmp_path / "audits"
        specs.mkdir()
        audits.mkdir()
        (audits / "2026-01-01-a.md").write_text("# An audit\n", encoding="utf-8")
        (specs / "2026-01-01-s-design.md").write_text("# A spec\n", encoding="utf-8")
        argv = ["--audit-dir", str(audits), "--spec-dir", str(specs)]

        assert main([*argv, "--check"]) == 1
        assert main(argv) == 0
        assert main([*argv, "--check"]) == 0

    def test_check_fails_once_a_new_doc_lands(self, tmp_path: Path) -> None:
        specs = tmp_path / "specs"
        audits = tmp_path / "audits"
        specs.mkdir()
        audits.mkdir()
        (audits / "2026-01-01-a.md").write_text("# An audit\n", encoding="utf-8")
        argv = ["--audit-dir", str(audits), "--spec-dir", str(specs)]
        assert main(argv) == 0

        (audits / "2026-02-02-b.md").write_text("# A later audit\n", encoding="utf-8")
        assert main([*argv, "--check"]) == 1


class TestCommittedIndexesAreCurrent:
    """The guard that makes drift loud — a new audit or spec fails CI until indexed.

    Without this the index is exactly the surface #599 warned about: declared,
    executed by nothing, asserted about by nothing, and therefore
    indistinguishable from a working one while it silently rots.
    """

    def test_indexes_match_the_corpus(self) -> None:
        expected = build_indexes(
            REPO_ROOT / "docs/audits", REPO_ROOT / "docs/superpowers/specs"
        )
        for path, content in expected.items():
            assert path.exists(), f"{path} is missing — run: make docs-index"
            assert path.read_text(encoding="utf-8") == content, (
                f"{path} is stale — run: make docs-index"
            )


class TestWrappedProse:
    """Regression: this corpus hard-wraps at ~80 columns.

    Reading a single line out of a Verdict section produced mid-sentence
    fragments in the first generated index — "(`DSR >= 0.95 …`). This lands on
    §8's third row:" — which reads as a truncated verdict rather than an opening.
    """

    def test_joins_a_wrapped_paragraph(self) -> None:
        text = (
            "# Doc\n\n## Verdict\n\n"
            "The sleeve fails the de-biased gate on every one of the ten\n"
            "pre-registered trials, and the failure is missing signal.\n"
        )
        verdict = verdict_from_markdown(text)
        assert verdict is not None
        assert "ten pre-registered trials" in verdict

    def test_a_wrapped_table_is_still_rejected(self) -> None:
        text = "# Doc\n\n## Verdict\n\n| Strategy | TF |\n| --- | --- |\n| bos | 1d |\n"
        assert verdict_from_markdown(text) is None


class TestTitleTrailingLabel:
    def test_strips_the_colon_left_by_a_verdict_label(self) -> None:
        assert title_from_markdown(
            "# D1 — Spot-perp CVD divergence sleeve: VERDICT\n"
        ) == ("D1 — Spot-perp CVD divergence sleeve")
