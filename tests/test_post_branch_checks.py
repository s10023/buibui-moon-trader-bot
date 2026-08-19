"""Tests for the extracted `/post-branch` mechanical checks.

Every check here ships a **positive control** — an input that makes it fire.
The prose versions of these checks had none, which is how the skill's own
non-Python file check shipped reporting COVERED for a file no doc had heard of:
it was never run against something that should fail.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from tools.agents_config import Budgets
from tools.post_branch_checks import (
    Runner,
    added_paths,
    bad_atx_lines,
    check_handoff_symbols,
    check_negative_claims,
    check_new_files,
    check_new_modules,
    check_new_targets,
    check_queue_items,
    current_state_bullets,
    extract_tokens,
    load_sensitive_terms,
    mask_term,
    numbered_items,
    probe_names,
    sensitive_terms_result,
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
            [".claude/skills/zzz-fake-skill/SKILL.md"], GENERIC_SKILL_SENTENCE
        )
        assert len(found) == 1
        assert "zzz-fake-skill" in found[0].detail

    def test_a_skill_the_docs_actually_name_is_covered(self) -> None:
        blob = GENERIC_SKILL_SENTENCE + "\nThe `post-branch` skill sweeps docs."
        assert check_new_files([".claude/skills/post-branch/SKILL.md"], blob) == []

    def test_ordinary_operator_file_still_reports_when_absent(self) -> None:
        found = check_new_files(["deploy/notify-failure.sh"], "unrelated prose")
        assert len(found) == 1

    def test_ordinary_operator_file_is_covered_when_named(self) -> None:
        assert (
            check_new_files(["deploy/notify-failure.sh"], "runs notify-failure.sh")
            == []
        )

    def test_tests_and_docs_and_python_are_out_of_scope(self) -> None:
        assert check_new_files(["tests/test_x.py", "docs/a.md", "tools/x.py"], "") == []


class TestCheckNewModules:
    def test_undocumented_module_fires(self) -> None:
        found = check_new_modules(["analytics/newsleeve/book.py"], "nothing here")
        assert len(found) == 1

    def test_documented_module_is_quiet(self) -> None:
        assert (
            check_new_modules(["analytics/regime.py"], "`regime.py` labels bars") == []
        )

    def test_word_boundary_stops_a_truncated_probe_reporting_covered(self) -> None:
        """`-w` semantics: a substring hit must NOT count as documentation."""
        assert (
            len(check_new_modules(["tools/docs_index.py"], "ocs_index is great")) == 1
        )


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
        findings, suppressed = check_negative_claims(
            self._runner(self.CLAIM),
            diff="+def pead_wiring() -> None:\n",
            diff_names="analytics/signal/pead_wiring.py",
        )
        assert len(findings) == 1
        assert "pead_wiring" in findings[0].detail
        assert suppressed == 0

    def test_a_claim_unrelated_to_the_diff_is_scoped_out_and_COUNTED(self) -> None:
        findings, suppressed = check_negative_claims(
            self._runner(self.CLAIM),
            diff="+def something_else() -> None:\n",
            diff_names="analytics/other.py",
        )
        assert findings == []
        assert suppressed == 1, "a scoped-out claim must stay countable, not vanish"

    def test_a_claim_with_no_token_FAILS_OPEN(self) -> None:
        """Unscopable means unruled-out; a miss is the harm this check exists for."""
        findings, suppressed = check_negative_claims(
            self._runner("docs/x.md:3:the exporter is not yet wired"),
            diff="+unrelated\n",
            diff_names="other.py",
        )
        assert len(findings) == 1
        assert "no token to scope on" in findings[0].detail
        assert suppressed == 0

    def test_a_REMOVAL_does_not_report_the_claim(self) -> None:
        """Removing the named thing makes an absence claim MORE true, not less."""
        findings, suppressed = check_negative_claims(
            self._runner(self.CLAIM),
            diff="-def pead_wiring() -> None:\n",
            diff_names="",
        )
        assert findings == []
        assert suppressed == 1

    def test_the_skill_itself_is_still_exempt(self) -> None:
        """post-branch's own file documents the language and must not self-match."""
        findings, suppressed = check_negative_claims(
            self._runner(
                ".claude/skills/post-branch/SKILL.md:9:`pead_wiring` is not yet wired"
            ),
            diff="+def pead_wiring() -> None:\n",
            diff_names="analytics/signal/pead_wiring.py",
        )
        assert findings == []
        assert suppressed == 0


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
