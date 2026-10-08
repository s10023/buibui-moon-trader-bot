"""Tests for the extracted `/post-branch` mechanical checks.

Every check here ships a **positive control** — an input that makes it fire.
The prose versions of these checks had none, which is how the skill's own
non-Python file check shipped reporting COVERED for a file no doc had heard of:
it was never run against something that should fail.
"""

from __future__ import annotations

import dataclasses
import io
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from tools.agents_config import Budgets
from tools.post_branch_checks import (
    NEGATIVE_CLAIM_EXEMPT,
    NEGATIVE_CLAIM_RE,
    UNCOVERED_STEPS,
    Runner,
    _negative_claims_result,
    added_paths,
    bad_atx_lines,
    changed_line_numbers,
    check_amended_targets,
    check_handoff_symbols,
    check_negative_claims,
    check_new_files,
    check_new_modules,
    check_new_targets,
    check_packages,
    check_queue_items,
    current_state_bullets,
    extract_tokens,
    load_sensitive_terms,
    main,
    mask_term,
    numbered_items,
    probe_names,
    render,
    scan_text_for_terms,
    sensitive_terms_result,
    sensitive_text_result,
    targets_by_line,
    uncovered_notice,
)

# CLAUDE.md's real sentence — the one that made every skill report COVERED.
GENERIC_SKILL_SENTENCE = (
    "Skills live in `.claude/skills/<name>/SKILL.md`, are invoked with "
    "`/skill-name`, and each one's description is already loaded."
)


class TestProbeNames:
    """The defect: a basename that names a ROLE cannot identify a FILE."""

    def test_shared_constant_probes_the_parent_directory(self) -> None:
        assert probe_names(".claude/skills/zzz-fake-skill/SKILL.md") == [
            "zzz-fake-skill"
        ]

    def test_every_shared_constant_behaves_the_same_way(self) -> None:
        for path, want in [
            ("pkg/__init__.py", "pkg"),
            ("docs/audits/INDEX.md", "audits"),
            ("web/ui/README.md", "ui"),
        ]:
            assert probe_names(path) == [want], path

    def test_ordinary_file_probes_its_own_name_and_stem(self) -> None:
        assert probe_names("deploy/backup-offsite.sh") == [
            "backup-offsite.sh",
            "backup-offsite",
        ]

    def test_bare_name_without_directory_does_not_probe_empty_string(self) -> None:
        assert "" not in probe_names("SKILL.md")


class TestCheckNewFiles:
    """The 4-for-4 the fix was tested against, now pinned."""

    def test_fabricated_skill_is_UNDOCUMENTED_despite_the_generic_sentence(
        self,
    ) -> None:
        found = check_new_files(
            [".claude/skills/zzz-fake-skill/SKILL.md"],
            GENERIC_SKILL_SENTENCE,
            tracked=(),
        )
        assert len(found) == 1
        assert "zzz-fake-skill" in found[0].detail

    def test_a_skill_the_docs_actually_name_is_covered(self) -> None:
        blob = GENERIC_SKILL_SENTENCE + "\nThe `post-branch` skill sweeps docs."
        assert (
            check_new_files([".claude/skills/post-branch/SKILL.md"], blob, tracked=())
            == []
        )

    def test_ordinary_operator_file_still_reports_when_absent(self) -> None:
        found = check_new_files(
            ["deploy/notify-failure.sh"], "unrelated prose", tracked=()
        )
        assert len(found) == 1

    def test_ordinary_operator_file_is_covered_when_named(self) -> None:
        assert (
            check_new_files(
                ["deploy/notify-failure.sh"], "runs notify-failure.sh", tracked=()
            )
            == []
        )

    def test_an_unindexed_docs_tree_is_still_checked(self) -> None:
        """`docs/research/` has no CI-gated index, so it is not exempt."""
        found = check_new_files(
            ["docs/research/2026-10-08-x.md"], "unrelated", tracked=()
        )
        assert [f.detail for f in found] == [
            "UNDOCUMENTED FILE: docs/research/2026-10-08-x.md"
        ]

    def test_the_two_indexed_docs_trees_are_exempt(self) -> None:
        added = [
            "docs/audits/2026-10-08-x.md",
            "docs/superpowers/specs/2026-10-08-y.md",
        ]
        assert check_new_files(added, "", tracked=()) == []

    def test_tests_and_docs_and_python_are_out_of_scope(self) -> None:
        assert (
            check_new_files(
                ["tests/test_x.py", "docs/audits/a.md", "tools/x.py"], "", tracked=()
            )
            == []
        )


class TestCheckNewModules:
    def test_undocumented_module_fires(self) -> None:
        found = check_new_modules(
            ["analytics/newsleeve/book.py"], "nothing here", tracked=()
        )
        assert len(found) == 1

    def test_documented_module_is_quiet(self) -> None:
        assert (
            check_new_modules(
                ["analytics/regime.py"], "`regime.py` labels bars", tracked=()
            )
            == []
        )

    def test_word_boundary_stops_a_truncated_probe_reporting_covered(self) -> None:
        """`-w` semantics: a substring hit must NOT count as documentation."""
        assert (
            len(
                check_new_modules(
                    ["tools/docs_index.py"], "ocs_index is great", tracked=()
                )
            )
            == 1
        )


class TestThreeOutcomes:
    """ST148 M8: the AMBIGUOUS outcome Step 4's prose had and the code lacked."""

    TRACKED = ("utils/telegram.py", "signals/report.py", "analytics/xsmom/report.py")

    def test_a_basename_shared_with_a_documented_sibling_is_AMBIGUOUS(self) -> None:
        """The #643 miss: `card/telegram.py` read COVERED off `utils/telegram.py`."""
        blob = "`utils/telegram.py` sends parse_mode=HTML."
        found = check_new_modules(["card/telegram.py"], blob, tracked=self.TRACKED)
        assert [f.detail for f in found] == [
            "AMBIGUOUS basename (verify by hand): card/telegram.py"
        ]

    def test_MUTATION_without_the_sibling_the_same_hit_is_credited(self) -> None:
        """The sibling, not the fixture prose, is what decides AMBIGUOUS."""
        blob = "`utils/telegram.py` sends parse_mode=HTML."
        assert check_new_modules(["card/telegram.py"], blob, tracked=()) == []

    def test_naming_the_full_path_beats_a_colliding_sibling(self) -> None:
        blob = "`utils/telegram.py` and `card/telegram.py` render differently."
        assert check_new_modules(["card/telegram.py"], blob, tracked=self.TRACKED) == []

    def test_a_dot_leading_path_is_credited_by_its_full_path(self) -> None:
        """A `\\b` bound never matches before `.claude`, the commonest citation."""
        path = ".claude/hooks/log-skill-usage.py"
        blob = f"see `{path}` for the ledger"
        tracked = (path, "other/log-skill-usage.py")
        assert check_new_modules([path], blob, tracked=tracked) == []

    def test_no_hit_at_all_stays_UNDOCUMENTED_whatever_the_siblings(self) -> None:
        found = check_new_files(["deploy/report.sh"], "nothing", tracked=self.TRACKED)
        assert [f.detail for f in found] == ["UNDOCUMENTED FILE: deploy/report.sh"]

    def test_an_operator_file_with_a_colliding_basename_is_AMBIGUOUS(self) -> None:
        tracked = ("deploy/windows/run-job.sh", "deploy/run-job.sh")
        found = check_new_files(
            ["deploy/windows/run-job.sh"], "wraps run-job.sh", tracked=tracked
        )
        assert [f.detail for f in found] == [
            "AMBIGUOUS basename (verify by hand): deploy/windows/run-job.sh"
        ]

    def test_a_shared_constant_basename_is_not_a_collision(self) -> None:
        """Every skill is SKILL.md; its identity is the directory, probed already."""
        tracked = (".claude/skills/card/SKILL.md", ".claude/skills/new/SKILL.md")
        assert (
            check_new_files(
                [".claude/skills/new/SKILL.md"], "the `new` skill", tracked=tracked
            )
            == []
        )

    def test_the_file_itself_in_tracked_is_not_its_own_sibling(self) -> None:
        tracked = ("tools/skill_usage.py",)
        assert (
            check_new_modules(
                ["tools/skill_usage.py"], "`skill_usage.py`", tracked=tracked
            )
            == []
        )


class TestGatherSeesUntrackedPackages:
    def test_status_lists_untracked_files_individually(self) -> None:
        """A new untracked directory must reach the legs file by file.

        Plain `git status --porcelain` collapses it to `?? dir/`, which
        `added_paths` skips -- measured: a probe `zzprobe/a.sh` read clean on
        both `packages` and `new-files`.
        """
        from tools import post_branch_checks

        seen: list[list[str]] = []

        def stub(argv: Sequence[str]) -> str:
            seen.append(list(argv))
            return ""

        post_branch_checks.gather(stub)
        status = [a for a in seen if a[:2] == ["git", "status"]]
        assert status and all("--untracked-files=all" in a for a in status)


class TestCheckPackages:
    def test_an_undocumented_package_fires(self) -> None:
        found = check_packages(
            ["newsleeve/book.py", "card/card.py"], "`card/` holds F2"
        )
        assert [f.detail for f in found] == ["UNDOCUMENTED: newsleeve/"]

    def test_non_package_and_dot_dirs_are_skipped(self) -> None:
        paths = [
            "tests/a.py",
            "docs/a.md",
            "config/a.toml",
            "scripts/a.py",
            ".claude/x",
        ]
        assert check_packages(paths, "") == []

    def test_top_level_files_are_not_packages(self) -> None:
        assert check_packages(["Makefile", "buibui.py"], "") == []

    def test_word_bound_stops_a_longer_name_reporting_covered(self) -> None:
        """`state_audit` inside `premium_state_audit` must not count."""
        found = check_packages(["state_audit/x.py"], "see premium_state_audit")
        assert len(found) == 1

    def test_live_tree_is_green(self) -> None:
        """The leg must not land permanently red on the tree it ships in."""

        root = Path(__file__).resolve().parents[1]
        tracked = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True, check=True, cwd=root
        ).stdout.split()
        blob = "\n".join(
            p.read_text(encoding="utf-8")
            for p in sorted((root / ".claude" / "context").glob("*.md"))
        )
        assert check_packages(tracked, blob) == []


class TestCheckNewTargets:
    def test_undocumented_target_fires(self) -> None:
        found = check_new_targets("+zzz-nope: x\n", "unrelated docs")
        assert len(found) == 1
        assert "zzz-nope" in found[0].detail

    def test_buibui_prefix_is_stripped_before_probing(self) -> None:
        """AGENTS.md documents subcommands; the wrapper rule covers the rest."""
        assert (
            check_new_targets("+buibui-param-audit: x\n", "`param-audit` runs WFO")
            == []
        )

    def test_unchanged_target_lines_are_ignored(self) -> None:
        assert check_new_targets(" existing-target: x\n", "") == []

    def test_a_yaml_key_is_not_a_make_target(self) -> None:
        """Found by running the sweep on its own branch.

        The regex was applied to the WHOLE diff, so `behavior_signal_globs:` in a
        SKILL.md's YAML block reported as an undocumented Make target. A target
        exists only in the Makefile, so `gather` now passes the Makefile diff
        alone — this test pins the shape that broke it.
        """
        yaml_hunk = "+behavior_signal_globs:   # touching these needs a walk\n"
        assert check_new_targets(yaml_hunk, "") != []  # the regex still matches...
        # ...which is exactly why the CALLER must scope it to the Makefile.


class TestCheckQueueItems:
    """Defect 1: nothing swept the handoff for work the branch just finished."""

    HANDOFF = (
        "## Next tasks\n\n"
        "1. **H-004 risk-off sector rotation is the NEXT TASK.** Start from\n"
        "   `config/universe.json`, which carries GICS sector.\n\n"
        "2. **Something unrelated** about `deploy/backup-offsite.sh`.\n"
    )

    def test_a_docs_only_branch_closing_an_item_is_CAUGHT(self) -> None:
        """The case the symbol-based mitigation structurally could not see.

        No `.py`, no `def` — only prose mentioning H-004.
        """
        diff = "+# H-004 verdict: BLOCKED at G3\n"
        found = check_queue_items(self.HANDOFF, diff, "docs/audits/h004.md")
        assert len(found) == 1
        assert "item 1" in found[0].detail

    def test_an_unrelated_branch_does_not_fire(self) -> None:
        diff = "+def unrelated() -> None:\n+    pass\n"
        assert check_queue_items(self.HANDOFF, diff, "analytics/x.py") == []

    def test_it_matches_on_a_changed_FILENAME_too(self) -> None:
        found = check_queue_items(self.HANDOFF, "", "deploy/backup-offsite.sh")
        assert len(found) == 1
        assert "item 2" in found[0].detail


class TestNumberedItems:
    def test_continuation_lines_join_their_item(self) -> None:
        items = numbered_items("1. first\n   more of first\n2. second\n")
        assert items[1] == "first\nmore of first"
        assert items[2] == "second"

    def test_a_new_unindented_paragraph_ends_the_item(self) -> None:
        items = numbered_items("1. first\n\nUnindented prose.\n2. second\n")
        assert "Unindented" not in items[1]


class TestAddedPaths:
    """An untracked file is exactly the case the presence checks exist for."""

    def test_untracked_files_count_as_added(self) -> None:
        assert added_paths("", "?? tools/brand_new.py\n") == ["tools/brand_new.py"]

    def test_untracked_directories_are_skipped(self) -> None:
        assert added_paths("", "?? somedir/\n") == []

    def test_staged_and_diffed_additions_merge_without_duplicates(self) -> None:
        assert added_paths("a.py\n", "A  a.py\n") == ["a.py"]

    def test_modified_files_are_not_additions(self) -> None:
        assert added_paths("", " M existing.py\n") == []


class TestHandoffSymbolNoise:
    """Ubiquitous doc stems discriminate nothing and drown the real hits."""

    def test_ubiquitous_stems_are_suppressed(self) -> None:
        handoff = "line about CLAUDE and SKILL and tools\n"
        found = check_handoff_symbols(handoff, "", "CLAUDE.md SKILL.md tools/x.py")
        assert found == []

    def test_a_real_symbol_still_reports(self) -> None:
        handoff = "the `powered_null` helper is absent here\n"
        found = check_handoff_symbols(handoff, "+def powered_null() -> None:\n", "")
        assert len(found) == 1


class TestExtractTokens:
    def test_backticked_spans_become_tokens(self) -> None:
        assert "universe.json" in extract_tokens("see `universe.json` for sectors")

    def test_hypothesis_ids_are_tokens_even_unbackticked(self) -> None:
        assert "H-004" in extract_tokens("H-004 is the next task")

    def test_stopwords_and_short_tokens_are_dropped(self) -> None:
        toks = extract_tokens("`main` and `md` and `git`")
        assert toks == set()

    def test_a_backticked_command_yields_its_first_word(self) -> None:
        assert "docs-index" in extract_tokens("run `docs-index --check` first")


class TestCurrentStateBullets:
    MEM = (
        "## Something\n- not counted\n\n"
        "## Current State\n\n> a quote\n\n- one\n- two\n  - nested not counted\n\n"
        "## After\n- also not counted\n"
    )

    def test_counts_only_top_level_bullets_in_the_section(self) -> None:
        assert current_state_bullets(self.MEM) == 2

    def test_absent_section_counts_zero(self) -> None:
        assert current_state_bullets("# nothing here\n") == 0


class TestBadAtxLines:
    def test_a_wrapped_pr_reference_in_column_one_is_flagged(self) -> None:
        assert bad_atx_lines("fine line\n#198 wrapped here\n") == [2]

    def test_a_real_heading_is_not_flagged(self) -> None:
        assert bad_atx_lines("# Real Heading\n## Also real\n") == []


class TestCheckNegativeClaims:
    """The scope leg. This check had NO test, which is how it ran unscoped.

    It greps the tree for absence language and used to report every hit on
    every branch — the same findings forever, regardless of the diff, while
    the skill's own table described it as asking about what the branch just
    added. Code and sentence disagreed and only the sentence was read.
    """

    CLAIM = (
        "docs/x.md:12:the `pead_wiring` module is not yet wired, so nothing reads it"
    )

    @staticmethod
    def _runner(out: str) -> Runner:
        def run(argv: Sequence[str]) -> str:
            return out

        return run

    def test_a_claim_the_branch_CONTRADICTS_is_reported(self) -> None:
        """Positive control: the branch adds the very thing the doc denies."""
        findings, suppressed, _exempted = check_negative_claims(
            self._runner(self.CLAIM),
            diff="+def pead_wiring() -> None:\n",
            diff_names="analytics/signal/pead_wiring.py",
        )
        assert len(findings) == 1
        assert "pead_wiring" in findings[0].detail
        assert suppressed == 0

    def test_a_claim_unrelated_to_the_diff_is_scoped_out_and_COUNTED(self) -> None:
        findings, suppressed, _exempted = check_negative_claims(
            self._runner(self.CLAIM),
            diff="+def something_else() -> None:\n",
            diff_names="analytics/other.py",
        )
        assert findings == []
        assert suppressed == 1, "a scoped-out claim must stay countable, not vanish"

    def test_a_claim_with_no_token_FAILS_OPEN(self) -> None:
        """Unscopable means unruled-out; a miss is the harm this check exists for."""
        findings, suppressed, _exempted = check_negative_claims(
            self._runner("docs/x.md:3:the exporter is not yet wired"),
            diff="+unrelated\n",
            diff_names="other.py",
        )
        assert len(findings) == 1
        assert "no token to scope on" in findings[0].detail
        assert suppressed == 0

    def test_the_grep_ENUMERATES_the_corpus_rather_than_FILTERING_it(self) -> None:
        """The git grep pattern must drop no line — ``NEGATIVE_CLAIM_RE`` is the filter.

        This leg shipped ``-e "x"`` — the LETTER — so git dropped every line
        without an ``x`` before the regex ever ran. Measured on this tree at the
        fix: **1,757 of 13,615 corpus lines survived (12.9%)**, and the leg
        matched **1 real claim where 4 exist**.

        No existing test could see it: every one of them mocks the runner's
        OUTPUT, so the argv — the only place the defect lives — was never
        asserted on. That is the shape to copy for any check whose real work is
        done by a subprocess it does not own.
        """
        seen: list[Sequence[str]] = []

        def run(argv: Sequence[str]) -> str:
            seen.append(argv)
            return ""

        check_negative_claims(run, diff="", diff_names="")

        assert seen, "the leg must actually invoke the runner"
        argv = seen[0]
        pattern = argv[argv.index("-e") + 1]

        claim = "the `pead_wiring` module is not yet wired, so nothing reads it"
        assert NEGATIVE_CLAIM_RE.search(claim), "fixture must be a real claim"
        assert "x" not in claim, "fixture must lack the letter the bug filtered on"
        assert re.search(pattern, claim), (
            f"git grep -e {pattern!r} DROPS a real claim line before "
            "NEGATIVE_CLAIM_RE can see it — the grep must enumerate, not filter"
        )

    def test_a_REMOVAL_does_not_report_the_claim(self) -> None:
        """Removing the named thing makes an absence claim MORE true, not less."""
        findings, suppressed, _exempted = check_negative_claims(
            self._runner(self.CLAIM),
            diff="-def pead_wiring() -> None:\n",
            diff_names="",
        )
        assert findings == []
        assert suppressed == 1

    def test_the_skill_itself_is_still_exempt(self) -> None:
        """post-branch's own file documents the language and must not self-match."""
        findings, suppressed, _exempted = check_negative_claims(
            self._runner(
                ".claude/skills/post-branch/SKILL.md:9:`pead_wiring` is not yet wired"
            ),
            diff="+def pead_wiring() -> None:\n",
            diff_names="analytics/signal/pead_wiring.py",
        )
        assert findings == []
        assert suppressed == 0


class TestNegativeClaimExempt:
    """The allowlist that keeps the leg readable without making it a mute.

    Measured here, not inherited: this branch added ONE Make target, which edits
    the `.PHONY` line — and `buibui-portfolio-replay` on that line scoped in
    `.claude/context/signals.md:135`, a 12,659-character paragraph carrying 130
    tokens whose absence claim ("only the card has no reader to weigh it") is
    about the pundit `avg_r` strip. Every branch that adds a target reproduces
    it, so the finding is a false positive BY CONSTRUCTION rather than by luck.

    ⚠ The tempting fix is the wrong one. Scoping on a window around the regex
    match rather than the whole line looks like the real remedy on a 12 KB line;
    the fork measured that it would have suppressed the leg's only true positive
    on record, whose falsified sentence sat ~1,400 characters from the match.
    The line stays the unit, and the narrowing is per `(path, token)`.
    """

    CLAIM = "docs/x.md:12:the `pead_wiring` module is not yet wired, `symbol` too"

    @staticmethod
    def _runner(out: str) -> Runner:
        def run(argv: Sequence[str]) -> str:
            return out

        return run

    def test_an_exempt_token_alone_is_counted_not_reported(self) -> None:
        exempt = dict(NEGATIVE_CLAIM_EXEMPT)
        exempt[("docs/x.md", "symbol")] = "test fixture"
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("tools.post_branch_checks.NEGATIVE_CLAIM_EXEMPT", exempt)
            findings, suppressed, exempted = check_negative_claims(
                self._runner(self.CLAIM),
                diff="+def uses(symbol: str) -> None:\n",
                diff_names="analytics/other.py",
            )
        assert findings == []
        assert exempted == 1, "an exemption must stay countable, never vanish"
        assert suppressed == 0

    def test_ONE_unexempt_token_still_reports_the_whole_line(self) -> None:
        """The property that stops the allowlist growing into a token filter."""
        exempt = dict(NEGATIVE_CLAIM_EXEMPT)
        exempt[("docs/x.md", "symbol")] = "test fixture"
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("tools.post_branch_checks.NEGATIVE_CLAIM_EXEMPT", exempt)
            findings, _suppressed, exempted = check_negative_claims(
                self._runner(self.CLAIM),
                diff="+def pead_wiring(symbol: str) -> None:\n",
                diff_names="analytics/signal/pead_wiring.py",
            )
        assert len(findings) == 1, "pead_wiring is not exempt, so the line reports"
        assert exempted == 0

    def test_the_exemption_is_keyed_on_the_PAIR_not_the_token(self) -> None:
        """The same token in ANOTHER file is untouched — no cross-file hole.

        Driven by the REAL table rather than a fixture token: a key-on-the-token
        mutation is invisible to a test whose token no entry carries, which is
        how this test passed while keyed-on-token silently worked.
        """
        if not NEGATIVE_CLAIM_EXEMPT:
            pytest.skip("allowlist empty — a silent pass here would be vacuous")
        for path, token in NEGATIVE_CLAIM_EXEMPT:
            elsewhere = "docs/somewhere-else.md"
            assert elsewhere != path
            findings, _suppressed, exempted = check_negative_claims(
                self._runner(f"{elsewhere}:3:`{token}` handling is not yet wired"),
                diff=f"+touches {token}\n",
                diff_names="analytics/other.py",
            )
            assert len(findings) == 1, f"{token} is exempt beyond {path}"
            assert exempted == 0

    def test_the_exempt_count_reaches_the_NOTE_rather_than_vanishing(self) -> None:
        """An allowlist nobody can see is a mute, which is what this is not."""
        exempt = dict(NEGATIVE_CLAIM_EXEMPT)
        exempt[("docs/x.md", "symbol")] = "test fixture"
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("tools.post_branch_checks.NEGATIVE_CLAIM_EXEMPT", exempt)
            result = _negative_claims_result(
                self._runner(self.CLAIM),
                diff="+def uses(symbol: str) -> None:\n",
                diff_names="analytics/other.py",
            )
        assert result.findings == []
        assert result.note is not None
        assert "NEGATIVE_CLAIM_EXEMPT" in result.note

    def test_every_entry_still_matches_a_REAL_claim_line(self) -> None:
        """External referent: a dead entry fails HERE rather than sitting silent.

        An allowlist nobody re-derives is how a mute survives the doc it was
        written for. Each `(path, token)` must still name a token carried by a
        line that actually trips the regex.
        """
        if not NEGATIVE_CLAIM_EXEMPT:
            pytest.skip("allowlist empty — a silent pass here would be vacuous")
        for (path, token), reason in NEGATIVE_CLAIM_EXEMPT.items():
            text = Path(path).read_text(encoding="utf-8")
            on_claim_lines = {
                tok
                for line in text.splitlines()
                if NEGATIVE_CLAIM_RE.search(line)
                for tok in extract_tokens(line)
            }
            assert token in on_claim_lines, (
                f"{path}:{token} exempts a token no claim line carries any more — "
                "delete the entry rather than leaving a mute behind"
            )
            assert reason.strip(), "every entry states why, inline"

    def test_an_exempted_LINE_still_reports_via_any_other_token(self) -> None:
        """The narrowing must not close the leg for the file it was written for.

        Positive control paired with each exemption, and derived from the REAL
        line rather than a fixture: scope the same line in by a token that is
        NOT exempt and it must still report. Without this, "no finding" cannot
        be told apart from "leg switched off for that document".
        """
        if not NEGATIVE_CLAIM_EXEMPT:
            pytest.skip("allowlist empty — a silent pass here would be vacuous")
        for path, token in NEGATIVE_CLAIM_EXEMPT:
            claim_line, lineno = self._real_claim_line(path, token)
            others = [
                t
                for t in extract_tokens(claim_line)
                if (path, t) not in NEGATIVE_CLAIM_EXEMPT
            ]
            assert others, f"{path}:{token} is the line's only token — that is a mute"
            findings, _suppressed, exempted = check_negative_claims(
                self._runner(f"{path}:{lineno}:{claim_line}"),
                diff=f"+touches {others[0]}\n",
                diff_names="analytics/other.py",
            )
            assert len(findings) == 1, f"{path}:{lineno} went silent on {others[0]}"
            assert exempted == 0

    @staticmethod
    def _real_claim_line(path: str, token: str) -> tuple[str, int]:
        """The live line the entry was written against, located by content."""
        for n, line in enumerate(
            Path(path).read_text(encoding="utf-8").splitlines(), 1
        ):
            if NEGATIVE_CLAIM_RE.search(line) and token in extract_tokens(line):
                return line, n
        raise AssertionError(f"{path} carries no claim line naming {token}")


class TestSensitiveTermList:
    """Parsing the gitignored term list."""

    def test_comments_blanks_and_case_are_normalised(self) -> None:
        raw = "# employer names\nAcmeCorp\n\n  widgetco  # former client\n"
        assert load_sensitive_terms(raw) == ["acmecorp", "widgetco"]

    def test_an_empty_list_is_empty_not_a_blank_term(self) -> None:
        assert load_sensitive_terms("# nothing yet\n\n") == []

    def test_mask_identifies_without_restating(self) -> None:
        """This output gets pasted into handoffs; a full term would re-leak it."""
        masked = mask_term("acmecorp")
        assert masked != "acmecorp"
        assert "acmecorp" not in masked
        assert masked.startswith("acm")


class TestSensitiveTermsGate:
    """The pre-flip gate itself.

    It exists because `git grep` on the working tree said clean for four months
    while three deleted spec docs kept two work-repo names reachable in history,
    and a visibility flip republishes the whole history, not just HEAD.
    """

    def test_a_missing_list_is_a_FINDING_not_a_skip(self) -> None:
        """The list is gitignored, so it dies on a reclone.

        Before a flip, "the gate did not run" and "the gate passed" must not
        look the same — a SKIP renders beside ten `clean` lines and reads as one.
        """
        result = sensitive_terms_result(lambda argv: "", terms=[])
        assert result.skipped is None
        assert len(result.findings) == 1
        assert "NOT CONFIGURED" in result.findings[0].detail

    def test_a_term_in_a_tracked_file_is_reported_masked(self) -> None:
        def runner(argv: Sequence[str]) -> str:
            return "docs/notes.md\n" if "grep" in argv else ""

        findings = sensitive_terms_result(runner, terms=["acmecorp"]).findings
        assert len(findings) == 1
        assert "docs/notes.md" in findings[0].detail
        assert "acmecorp" not in findings[0].detail

    def test_a_term_introduced_by_a_branch_commit_is_reported(self) -> None:
        def runner(argv: Sequence[str]) -> str:
            return "abc1234 add the thing\n" if "log" in argv else ""

        findings = sensitive_terms_result(runner, terms=["acmecorp"]).findings
        assert len(findings) == 1
        assert "history" in findings[0].detail

    def test_clean_states_what_was_and_was_not_checked(self) -> None:
        result = sensitive_terms_result(lambda argv: "", terms=["acmecorp"])
        assert result.findings == []
        assert result.note is not None and "baseline" in result.note


class TestSensitiveTermsCoversCommitMessages:
    """A commit MESSAGE is a surface no file deletion reaches.

    Found by scanning for real: three commits in this repo's history name a
    work repo in their subject line, and the gate's own scrub commit did too —
    it read clean while its message carried the term. Deleting the file never
    removes it; only a history rewrite does, which is exactly why it has to be
    caught BEFORE the flip rather than triaged after.
    """

    def test_a_term_in_a_branch_commit_message_is_reported(self) -> None:
        def runner(argv: Sequence[str]) -> str:
            if "--format=%B%n%s" in argv:
                return "docs: drop the acmecorp comparison table\n"
            return ""

        findings = sensitive_terms_result(runner, terms=["acmecorp"]).findings
        assert len(findings) == 1
        assert "MESSAGE" in findings[0].detail
        assert "acmecorp" not in findings[0].detail

    def test_a_clean_message_log_reports_nothing(self) -> None:
        def runner(argv: Sequence[str]) -> str:
            return "docs: tidy the tables\n" if "--format=%B%n%s" in argv else ""

        assert sensitive_terms_result(runner, terms=["acmecorp"]).findings == []


class TestSensitiveTextScan:
    """The FOURTH exposure surface: a PR title/body, before it is posted.

    The three git legs above ask about the tracked tree and this branch's
    commits. A PR title and body are neither, so the gate reports `clean` on a
    body naming every term — correctly, and uselessly. It is also the only
    INDEXABLE one of the four: this repo flips public to run CI, and a posted
    body is public the moment it lands.
    """

    TERM = "acmecorp"

    def test_a_term_in_a_composed_body_FIRES(self) -> None:
        """Positive control for the surface the git legs cannot reach."""
        result = sensitive_text_result(
            [("pr-body.md", f"## Summary\n\nPorted from {self.TERM}'s tooling.\n")],
            terms=[self.TERM],
        )
        assert len(result.findings) == 1
        assert "line(s) 3" in result.findings[0].detail

    def test_the_term_is_NEVER_printed_unmasked(self) -> None:
        """This report is itself pasted into handoffs, so it must not restate."""
        result = sensitive_text_result(
            [("pr-body.md", f"{self.TERM}\n")], terms=[self.TERM]
        )
        assert self.TERM not in result.findings[0].detail
        assert "acm" in result.findings[0].detail

    def test_the_matching_LINE_is_never_echoed(self) -> None:
        """Line numbers only: quoting context re-exposes what masking withheld."""
        result = sensitive_text_result(
            [("pr-body.md", f"we vendored {self.TERM} internals here")],
            terms=[self.TERM],
        )
        assert "vendored" not in result.findings[0].detail

    def test_a_clean_body_is_clean_and_says_what_it_checked(self) -> None:
        result = sensitive_text_result(
            [("pr-body.md", "## Summary\n\nNothing to see.\n")], terms=[self.TERM]
        )
        assert result.findings == []
        assert result.note is not None and "1 term(s)" in result.note

    def test_an_absent_list_is_a_FINDING_here_too(self) -> None:
        """`--text` must not become the one mode where NOT CONFIGURED reads as a pass."""
        result = sensitive_text_result([("pr-body.md", "anything")], terms=[])
        assert result.skipped is None
        assert len(result.findings) == 1
        assert "NOT CONFIGURED" in result.findings[0].detail

    def test_case_is_ignored_and_every_hit_line_is_listed(self) -> None:
        text = f"{self.TERM.upper()}\nfiller\nand {self.TERM.title()} again\n"
        found = scan_text_for_terms("body", text, [self.TERM])
        assert len(found) == 1
        assert "line(s) 1, 3" in found[0].detail

    def test_a_title_and_a_body_are_screened_in_ONE_run(self) -> None:
        """Both halves post together, so both are screened together."""
        result = sensitive_text_result(
            [("title", f"fix: drop the {self.TERM} table"), ("body", "clean\n")],
            terms=[self.TERM],
        )
        assert len(result.findings) == 1
        assert "title" in result.findings[0].detail


class TestSensitiveTextCli:
    """`--text` end to end — the mode a session runs before `gh pr create`."""

    TERM = "acmecorp"

    @staticmethod
    def _terms_file(tmp_path: Path) -> Path:
        f = tmp_path / "terms.txt"
        f.write_text(f"{TestSensitiveTextCli.TERM}\n", encoding="utf-8")
        return f

    def test_exit_1_on_a_hit_and_0_when_clean(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        dirty = tmp_path / "dirty.md"
        dirty.write_text(f"ported from {self.TERM}\n", encoding="utf-8")
        clean = tmp_path / "clean.md"
        clean.write_text("nothing here\n", encoding="utf-8")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "tools.post_branch_checks.SENSITIVE_TERMS", self._terms_file(tmp_path)
            )
            assert main(["--text", str(dirty)]) == 1
            assert main(["--text", str(clean)]) == 0
        assert self.TERM not in capsys.readouterr().out

    def test_a_dash_reads_STDIN_so_a_title_pipes_straight_in(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`FILE=-` is what the Make target documents; an undocumented-by-test
        path is how a advertised mode rots."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "tools.post_branch_checks.SENSITIVE_TERMS", self._terms_file(tmp_path)
            )
            mp.setattr("sys.stdin", io.StringIO(f"fix: drop the {self.TERM} table\n"))
            assert main(["--text", "-"]) == 1
        out = capsys.readouterr().out
        assert self.TERM not in out
        # `in - at line(s) 1` reads as a dash in prose, and this line is what an
        # operator triages seconds before a visibility flip.
        assert "in stdin at line(s) 1" in out

    def test_it_REFUSES_to_run_alongside_check(self, tmp_path: Path) -> None:
        """Honouring one flag and dropping the other reports a pass unasked for."""
        body = tmp_path / "b.md"
        body.write_text("clean\n", encoding="utf-8")
        assert main(["--text", str(body), "--check", "md-atx"]) == 2

    def test_an_UNREADABLE_file_exits_2_and_never_renders_clean(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A read error must not render as a clean run of a one-check sweep."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "tools.post_branch_checks.SENSITIVE_TERMS", self._terms_file(tmp_path)
            )
            assert main(["--text", str(tmp_path / "nope.md")]) == 2
        assert "clean" not in capsys.readouterr().out

    def test_it_runs_with_NO_git_surface_at_all(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The sweep's other legs shell out to git; this mode must not.

        A pre-`gh pr create` screen has to work on a body drafted anywhere —
        including outside a repo — so a git call here is a latent failure.
        """
        body = tmp_path / "b.md"
        body.write_text("nothing here\n", encoding="utf-8")

        def explode(*args: object, **kwargs: object) -> None:
            raise AssertionError("--text must not shell out")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "tools.post_branch_checks.SENSITIVE_TERMS", self._terms_file(tmp_path)
            )
            mp.setattr("tools.post_branch_checks.subprocess.run", explode)
            assert main(["--text", str(body)]) == 0
        assert "clean" in capsys.readouterr().out


class TestSurfaceListsComeFromConfig:
    """The mutation control for the repoint.

    Asserting the tuples merely *contain* the right paths passes just as well
    against a surviving hardcoded copy. These assert they are DERIVED.
    """

    def test_anchor_files_matches_the_config_view(self) -> None:
        from tools import agents_config, post_branch_checks

        cfg = agents_config.load(Path.cwd())
        assert post_branch_checks.anchor_files() == cfg.paths_with_role("anchor")

    def test_enumerating_docs_matches_the_config_view(self) -> None:
        from tools import agents_config, post_branch_checks

        cfg = agents_config.load(Path.cwd())
        assert post_branch_checks.enumerating_docs() == cfg.paths_with_role(
            "enumerating"
        )

    def test_negative_claim_paths_matches_the_config_view(self) -> None:
        from tools import agents_config, post_branch_checks

        cfg = agents_config.load(Path.cwd())
        assert post_branch_checks.negative_claim_paths() == cfg.paths_with_role(
            "negative_claim"
        )

    def test_a_config_failure_renders_as_a_finding_not_a_crash(self) -> None:
        """The whole reason the read is deferred to call time.

        An import-time load would crash the runner, and a swallowed one would
        print `0 findings, exit 0` — the SKIP-looks-like-PASS failure this repo
        has shipped twice. It must be neither.
        """
        from tools import post_branch_checks
        from tools.agents_config import ConfigError

        def stub(argv: Sequence[str]) -> str:
            return ""

        def boom() -> None:
            raise ConfigError("docs/agents/surfaces.toml is missing")

        results = post_branch_checks.gather(stub, load_config=boom)
        config_leg = [r for r in results if r.name == "agents-config"]
        assert len(config_leg) == 1
        assert config_leg[0].findings, "a missing config must FIRE, not skip"
        assert config_leg[0].skipped is None

    def test_the_view_is_not_a_hardcoded_copy(self, tmp_path: Path) -> None:
        """Drop a role from a fixture config; ``agents_config``'s own view must shrink.

        This exercises ``agents_config.load`` / ``paths_with_role`` only — it
        says nothing about whether ``post_branch_checks`` itself reads that
        view rather than a surviving literal tuple. That property is
        ``test_the_accessor_is_derived_not_literal``, below.
        """
        from tools import agents_config

        (tmp_path / "docs" / "agents").mkdir(parents=True)
        real = (Path.cwd() / agents_config.CONFIG).read_text(encoding="utf-8")
        (tmp_path / agents_config.CONFIG).write_text(
            real.replace('"anchor", ', "", 1), encoding="utf-8"
        )
        shrunk = agents_config.load(tmp_path)
        assert len(shrunk.paths_with_role("anchor")) < len(
            agents_config.load(Path.cwd()).paths_with_role("anchor")
        )

    def test_the_accessor_is_derived_not_literal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mutate the config the module reads; the accessor must move with it.

        A surviving hardcoded tuple passes every other test in this class and
        fails this one — it is the mutation control the other tests are not.
        """
        from tools import agents_config, post_branch_checks

        real = agents_config.load(Path.cwd())
        shrunk = dataclasses.replace(real, surfaces=real.surfaces[:1])
        monkeypatch.setattr(post_branch_checks, "load_agents_config", lambda: shrunk)
        post_branch_checks._cfg.cache_clear()
        try:
            assert len(post_branch_checks.anchor_files()) < len(
                real.paths_with_role("anchor")
            )
        finally:
            post_branch_checks._cfg.cache_clear()

    def test_importing_the_module_does_not_read_the_config(
        self, tmp_path: Path
    ) -> None:
        """The deferred read, mutation-tested.

        A module-level ``_CFG = load()`` crashes on import when no config is
        present — and this module is imported by the CI-gating sweep, so that
        crash replaces a readable finding with a traceback. Running the
        import from a directory with no config is the only way to tell the
        two apart; every in-process test in this file passes against both.
        """
        result = subprocess.run(
            [sys.executable, "-c", "import tools.post_branch_checks"],
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(Path.cwd())},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"importing the module read the config eagerly: {result.stderr}"
        )


class TestHandoffSize:
    """The leg this replaces returned [] on every input, and nothing tested it.

    A test asserting only "a small handoff is clean" would reproduce that
    defect exactly, so both firing cases come first.
    """

    def _budgets(self) -> Budgets:
        return Budgets(
            handoff_lines=10,
            handoff_ceiling=20,
            memory_state_bullets=6,
            memory_state_ceiling=8,
            memory_bytes_cap=17408,
        )

    def test_within_budget_is_clean(self) -> None:
        from tools.post_branch_checks import _check_handoff_size

        assert _check_handoff_size("x\n" * 5, self._budgets()) == []

    def test_over_budget_fires(self) -> None:
        from tools.post_branch_checks import _check_handoff_size

        found = _check_handoff_size("x\n" * 15, self._budgets())
        assert len(found) == 1
        assert "15 lines" in found[0].detail
        assert "budget 10" in found[0].detail

    def test_at_ceiling_fires_harder(self) -> None:
        from tools.post_branch_checks import _check_handoff_size

        found = _check_handoff_size("x\n" * 25, self._budgets())
        assert len(found) == 1
        assert "ceiling" in found[0].detail

    def test_empty_handoff_is_not_silently_clean(self) -> None:
        """An absent handoff is a finding: sessions get deleted without it."""
        from tools.post_branch_checks import _check_handoff_size

        found = _check_handoff_size("", self._budgets())
        assert len(found) == 1
        assert "absent" in found[0].detail

    def test_a_stamp_is_no_longer_consulted(self) -> None:
        """The old leg keyed on this line. A file carrying a wrong stamp but a
        fine size must now be clean — otherwise the stamp mechanism survived."""
        from tools.post_branch_checks import _check_handoff_size

        body = "Line count: **999**\n" + "x\n" * 4
        assert _check_handoff_size(body, self._budgets()) == []


class TestHandoffSizeSkipsOnlyWhenItKnowsWhyTheHandoffIsGone:
    """ST75. A leg that reds on the SETUP rather than on the branch is a leg
    that gets ignored, and a worktree has no gitignored handoff by
    construction. But a blanket skip would make absent-because-worktree and
    absent-because-nobody-wrote-one render alike in the other direction, which
    is the failure this leg exists to catch. So the skip has to NAME its
    reason, and every test below fixes one half of that against mutation.
    """

    def _budgets(self) -> Budgets:
        return Budgets(
            handoff_lines=10,
            handoff_ceiling=20,
            memory_state_bullets=6,
            memory_state_ceiling=8,
            memory_bytes_cap=17408,
        )

    def _runner(self, out: str) -> Runner:
        return lambda argv: out

    _WORKTREE = "/repo/.git/worktrees/wt\n/repo/.git\n"
    _MAIN = "/repo/.git\n/repo/.git\n"

    def test_absent_in_a_worktree_skips(self) -> None:
        from tools.post_branch_checks import _handoff_size_result

        r = _handoff_size_result("", self._runner(self._WORKTREE), self._budgets())
        assert r.findings == []
        assert r.skipped is not None
        assert "worktree" in r.skipped

    def test_absent_in_a_main_checkout_still_fires(self) -> None:
        """The teeth. An unconditional skip passes every other test here."""
        from tools.post_branch_checks import _handoff_size_result

        r = _handoff_size_result("", self._runner(self._MAIN), self._budgets())
        assert r.skipped is None
        assert len(r.findings) == 1
        assert "absent" in r.findings[0].detail

    def test_a_worktree_does_not_excuse_an_oversized_handoff(self) -> None:
        """Scope. The skip keys on ABSENCE, not on being in a worktree — a
        worktree that does carry a handoff is sized like anywhere else."""
        from tools.post_branch_checks import _handoff_size_result

        r = _handoff_size_result(
            "x\n" * 25, self._runner(self._WORKTREE), self._budgets()
        )
        assert r.skipped is None
        assert len(r.findings) == 1
        assert "ceiling" in r.findings[0].detail

    def test_unreadable_git_output_leaves_the_finding_standing(self) -> None:
        """Fail toward the finding: this call can only ever CLEAR a red, so an
        answer it cannot parse must not be the one that clears it."""
        from tools.post_branch_checks import _handoff_size_result

        for out in ("", "not a path\n", "/repo/.git\n/a\n/b\n"):
            r = _handoff_size_result("", self._runner(out), self._budgets())
            assert r.skipped is None, out
            assert len(r.findings) == 1, out

    def test_worktree_detection_is_pure_and_compares_resolved_paths(self) -> None:
        from tools.post_branch_checks import in_linked_worktree

        assert in_linked_worktree(self._WORKTREE) is True
        assert in_linked_worktree(self._MAIN) is False
        # Same directory spelled two ways is the MAIN checkout, not a worktree.
        assert in_linked_worktree("/repo/.git\n/repo/./.git\n") is False
        assert in_linked_worktree("") is False


class TestMemoryCapUsesConfiguredBudgets:
    def test_bullet_cap_comes_from_config(self) -> None:
        from tools.agents_config import Budgets

        b = Budgets(
            handoff_lines=200,
            handoff_ceiling=600,
            memory_state_bullets=2,
            memory_state_ceiling=3,
            memory_bytes_cap=17408,
        )
        assert b.memory_state_bullets == 2


def test_runs_as_a_bare_script_with_no_pythonpath() -> None:
    """CI invokes this as `python3 tools/post_branch_checks.py` with NO PYTHONPATH.

    The Make target sets `PYTHONPATH=.`, so a green `make post-branch-checks` says
    nothing about the invocation CI actually uses. That divergence shipped a red
    CI on a branch whose every local gate was green: adding a `tools.*` import to
    a script that had none put `tools/` on sys.path instead of the repo root, and
    the step failed on an import rather than on a finding.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "tools/post_branch_checks.py"],
        cwd=Path(__file__).resolve().parent.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert "ModuleNotFoundError" not in result.stderr, result.stderr
    assert "Traceback" not in result.stderr, result.stderr


class TestUncoveredNotice:
    """The sweep must name its own gaps, so passing it cannot FEEL like the walk.

    Filed as ST88 after BOTH parallel sessions on the 2026-08-25 wave made the
    same two misses: neither ran `make preflight` and neither invoked
    `/post-branch`, both hand-walking the mechanical half. Neither was being
    careless — the hand-walk is the reachable thing and the skill is not, so
    this is a reachability fix rather than another rule.
    """

    def test_the_full_sweep_names_what_it_does_not_cover(self) -> None:
        lines, _ = render([])
        assert any("NOT COVERED by this sweep" in ln for ln in lines)

    def test_it_names_make_preflight_the_specific_miss(self) -> None:
        """`make preflight` SUPERSEDES `make test` at the branch's final gate.

        A sweep that lists steps generically would not have interrupted the
        miss it was filed for, so this is pinned by name and not by count.
        """
        body = "\n".join(uncovered_notice())
        assert "make preflight" in body
        assert "gh pr create" in body

    def test_it_tells_the_reader_to_INVOKE_the_skill(self) -> None:
        body = "\n".join(uncovered_notice())
        assert "/post-branch" in body
        assert "not a substitute" in body

    def test_every_configured_step_reaches_the_output(self) -> None:
        """Derived from UNCOVERED_STEPS, never a hardcoded second copy."""
        body = "\n".join(uncovered_notice())
        for label, what in UNCOVERED_STEPS:
            assert label in body, f"{label} missing from the notice"
            assert what.split("(")[0].strip()[:20] in body

    def test_it_cites_STEPS_and_never_PHASE_anchors(self) -> None:
        """MUTATION guard on the trap this fix could itself fall into.

        The skill's phases 1-6 are table rows that declare no headings, so a
        "post-branch phase 4" citation from any other file is a dead anchor —
        `tools/stale_anchors.py` flags it, and it cost a peer session two
        rounds on #667. Adding a phase number here would reintroduce exactly
        that defect on the surface meant to prevent misses.
        """
        body = "\n".join(uncovered_notice()).lower()
        assert "phase" not in body, "cite Step N, never a phase anchor"
        assert "step" in body

    def test_the_notice_is_ABSENT_from_text_mode(self, tmp_path: Path) -> None:
        """`--text` screens one composed string seconds before a flip.

        It runs alone, needs no git surface, and gates on its own — the step
        list there is noise at the worst moment for noise.
        """
        p = tmp_path / "body.md"
        p.write_text(
            "A clean PR body with nothing sensitive in it.\n", encoding="utf-8"
        )
        buf = io.StringIO()
        stdout, sys.stdout = sys.stdout, buf
        try:
            main(["--text", str(p)])
        finally:
            sys.stdout = stdout
        assert "NOT COVERED" not in buf.getvalue()

    def test_a_SINGLE_check_still_names_the_gaps(self, tmp_path: Path) -> None:
        """`--check` covers even less of the walk, so the notice matters more."""
        lines, _ = render([], show_uncovered=True)
        assert any("NOT COVERED" in ln for ln in lines)


#: A Makefile shaped like the real one at the moment #746 amended it. Line 1 is
#: `.PHONY`, line 2 declares the target, lines 3-5 are its recipe, 7-8 a sibling.
_MAKEFILE = (
    ".PHONY: buibui-portfolio-replay\n"
    "buibui-portfolio-replay:\n"
    "\t@poetry run python buibui.py portfolio replay \\\n"
    "\t\t$(if $(CONFIG),--config $(CONFIG),) \\\n"
    "\t\t$(if $(DB),--db $(DB),)\n"
    "\n"
    "other-target:\n"
    "\t@echo hi\n"
)

#: The #746 diff: the recipe gains one override line. No target is ADDED.
_AMEND_DIFF = (
    "@@ -2,3 +2,4 @@\n"
    " buibui-portfolio-replay:\n"
    "\t@poetry run python buibui.py portfolio replay \\\n"
    "-\t\t$(if $(CONFIG),--config $(CONFIG),)\n"
    "+\t\t$(if $(CONFIG),--config $(CONFIG),) \\\n"
    "+\t\t$(if $(DB),--db $(DB),)\n"
)

_DOCS = {
    "AGENTS.md": "Wrapped by `make buibui-portfolio-replay` (`CONFIG=` / `CAPITAL=`).",
    "README.md": "make buibui-portfolio-replay CAPITAL=25000\n",
    "unrelated.md": "nothing to see",
}


class TestChangedLineNumbers:
    def test_added_lines_are_reported_in_new_file_coordinates(self) -> None:
        assert changed_line_numbers("@@ -1,1 +1,2 @@\n a\n+b\n") == {2}

    def test_a_deletion_blames_the_position_it_vacated(self) -> None:
        """A pure deletion has NO new-file line number of its own.

        Skipping it would make a recipe line REMOVED from a target invisible,
        which is the same amendment this leg exists to catch, arriving as a
        subtraction instead of an addition.
        """
        assert changed_line_numbers("@@ -1,2 +1,1 @@\n a\n-b\n") == {2}

    def test_file_headers_are_not_mistaken_for_added_lines(self) -> None:
        diff = "--- a/Makefile\n+++ b/Makefile\n@@ -1,1 +1,2 @@\n a\n+b\n"
        assert changed_line_numbers(diff) == {2}


class TestTargetsByLine:
    def test_recipe_lines_map_to_their_target(self) -> None:
        mapping = targets_by_line(_MAKEFILE)
        assert mapping[2] == "buibui-portfolio-replay"
        assert mapping[5] == "buibui-portfolio-replay"
        assert mapping[7] == "other-target"

    def test_phony_is_not_a_target_and_ends_attribution(self) -> None:
        """`.PHONY:` names targets; it is not one, and it is not a recipe."""
        assert targets_by_line(_MAKEFILE).get(1) is None


class TestCheckAmendedTargets:
    def test_the_746_shape_fires_and_names_every_doc_to_re_read(self) -> None:
        """The regression this leg exists for.

        #746 added `DB=` to an EXISTING target. `new-targets` only matches an
        added `^\\+target:` line, so it read clean while AGENTS.md and README.md
        both went one override short.
        """
        found = check_amended_targets(_AMEND_DIFF, _MAKEFILE, _DOCS)
        assert len(found) == 1
        assert "buibui-portfolio-replay" in found[0].detail
        assert "AGENTS.md" in found[0].detail
        assert "README.md" in found[0].detail
        assert "unrelated.md" not in found[0].detail

    def test_an_added_target_is_left_to_new_targets(self) -> None:
        """No double-reporting: `new-targets` already owns the added case."""
        diff = "@@ -6,0 +7,2 @@\n+other-target:\n+\t@echo hi\n"
        assert check_amended_targets(diff, _MAKEFILE, _DOCS) == []

    def test_a_target_no_doc_names_is_quiet(self) -> None:
        """Nothing can be stale about a target no doc enumerates."""
        diff = "@@ -7,2 +7,2 @@\n other-target:\n-\t@echo hi\n+\t@echo bye\n"
        assert check_amended_targets(diff, _MAKEFILE, _DOCS) == []

    def test_an_empty_diff_is_quiet(self) -> None:
        assert check_amended_targets("", _MAKEFILE, _DOCS) == []

    def test_a_non_recipe_line_is_not_attributed_to_a_target(self) -> None:
        """Editing `.PHONY` is not amending the target's behaviour."""
        diff = "@@ -1,1 +1,1 @@\n-.PHONY: buibui-portfolio-replay\n+.PHONY: x\n"
        assert check_amended_targets(diff, _MAKEFILE, _DOCS) == []

    def test_word_boundary_stops_a_substring_doc_hit(self) -> None:
        """MUTATION: a longer target name must not credit a shorter one's docs."""
        docs = {"AGENTS.md": "see `make buibui-portfolio-replay-extra` instead"}
        assert check_amended_targets(_AMEND_DIFF, _MAKEFILE, docs) == []


class TestCheckFlagIsRepeatable:
    """`--check` must accept several names, and reject an unknown one loudly.

    Declared without ``action="append"`` it kept the LAST value only, so
    ``--check memory-cap --check handoff-size`` ran ONE leg and printed a
    complete-looking clean sweep. Those are exactly the two legs
    ``/post-branch`` tells a session to re-read late in the run — the pair
    where emptiness reading as coverage is least likely to be noticed.
    """

    @staticmethod
    def _stub_gather(
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Three synthetic legs, so the assertions describe the FLAG only."""
        import tools.post_branch_checks as pbc

        monkeypatch.setattr(
            pbc,
            "gather",
            lambda: [
                pbc.CheckResult("alpha"),
                pbc.CheckResult("beta"),
                pbc.CheckResult("gamma"),
            ],
        )

    def test_two_names_run_both(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._stub_gather(monkeypatch)
        assert main(["--check", "alpha", "--check", "beta", "--exit-zero"]) == 0
        out = capsys.readouterr().out
        assert "alpha" in out
        assert "beta" in out
        assert "gamma" not in out

    def test_one_name_runs_only_that_one(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Positive control.

        Without this case the two-name assertion above would pass just as well
        against a tool that ignored ``--check`` entirely and ran everything.
        """
        self._stub_gather(monkeypatch)
        assert main(["--check", "beta", "--exit-zero"]) == 0
        out = capsys.readouterr().out
        assert "beta" in out
        assert "alpha" not in out
        assert "gamma" not in out

    def test_a_repeated_name_is_not_run_twice(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._stub_gather(monkeypatch)
        assert main(["--check", "alpha", "--check", "alpha", "--exit-zero"]) == 0
        assert capsys.readouterr().out.count("alpha") == 1

    def test_unknown_name_alone_aborts(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._stub_gather(monkeypatch)
        assert main(["--check", "nosuchleg", "--exit-zero"]) == 2
        assert "no such check: nosuchleg" in capsys.readouterr().err

    def test_unknown_name_AMONG_several_aborts_the_whole_run(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The case the empty-result guard could never catch.

        Filtering to the survivors would run ``alpha``, report clean, and let a
        typo pass as coverage — the same shape as the repeatability bug itself.
        """
        self._stub_gather(monkeypatch)
        assert main(["--check", "alpha", "--check", "nosuchleg", "--exit-zero"]) == 2
        captured = capsys.readouterr()
        assert "no such check: nosuchleg" in captured.err
        assert "alpha" not in captured.out
