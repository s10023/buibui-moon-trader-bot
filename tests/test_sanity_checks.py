"""Tests for `tools/sanity_checks.py`.

Every check gets a **positive control** — an input that must produce a finding —
alongside a clean case. A check exercised only against the real tree proves
nothing while the tree is clean, which is the state it is supposed to be in.

The last test is the gate itself: it runs the whole sweep against this working
tree and requires zero findings. That is what makes this a check rather than a
script, per CLAUDE.md's *a self-check outside CI is not a check* — it fails the
suite, and therefore CI, the moment a doc surface drifts.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.sanity_checks import (
    CheckResult,
    Finding,
    check_cli_documented,
    check_config_strategies,
    check_context_coverage,
    check_fork_drift,
    check_missing_paths,
    check_parent_leakage,
    check_router_wiring,
    gather,
    makefile_targets,
    render,
    subcommand_names,
    surface_paths,
)

TARGETS = {"test", "lint-py"}
TIMEFRAMES = {"4h", "1d", "1wk"}
STRATEGIES = {"doji", "orb"}
SYMBOLS = {"AAPL", "MSFT"}


_UNSET: set[str] = set()


def _drift(text: str, symbols: set[str] | None = _UNSET) -> list[Finding]:
    """`symbols` defaults to the fixture; pass None to exercise the skip.

    A plain `symbols or SYMBOLS` would collapse an explicit None back to the
    fixture, which is how the skip test passed without ever taking the branch.
    """
    resolved = SYMBOLS if symbols is _UNSET else symbols
    return check_fork_drift(
        [("doc.md", text)], TARGETS, TIMEFRAMES, STRATEGIES, resolved
    )


class TestForkDrift:
    def test_clean_document_yields_nothing(self) -> None:
        text = "Run `make test` with `--interval 1d --strategy doji --symbol AAPL`."
        assert _drift(text) == []

    def test_catches_a_dead_make_target(self) -> None:
        assert _drift("Run `make buibui-backtest` first.") == [
            Finding("fork-drift", "doc.md: make-target=buibui-backtest")
        ]

    def test_catches_a_rejected_timeframe(self) -> None:
        assert _drift("pass --interval 15m here") == [
            Finding("fork-drift", "doc.md: timeframe=15m")
        ]

    def test_catches_a_removed_strategy(self) -> None:
        assert _drift("--strategy liquidity_sweep") == [
            Finding("fork-drift", "doc.md: strategy=liquidity_sweep")
        ]

    def test_catches_a_foreign_symbol(self) -> None:
        assert _drift("--symbol BTCUSDT") == [
            Finding("fork-drift", "doc.md: symbol=BTCUSDT")
        ]

    def test_bare_prose_is_not_a_make_target(self) -> None:
        """The reason the pattern requires a backtick, `$ ` or line start.

        Widening past `.claude/` made bare prose 6 of 7 hits.
        """
        assert _drift("This should make sense and make money.") == []

    def test_glob_and_placeholder_targets_are_ignored(self) -> None:
        assert _drift("`make buibui-` covers each subcommand") == []

    def test_template_placeholders_are_not_strategies(self) -> None:
        assert _drift("--strategy my_strategy") == []

    def test_symbol_leg_is_skippable(self) -> None:
        """`symbols=None` is how the leg degrades where the watchlist is absent."""
        assert _drift("--symbol BTCUSDT", symbols=None) == []
        # ...and the other legs still run.
        assert (
            len(
                check_fork_drift(
                    [("doc.md", "`make buibui-x` --symbol BTCUSDT")],
                    TARGETS,
                    TIMEFRAMES,
                    STRATEGIES,
                    None,
                )
            )
            == 1
        )


class TestParentLeakage:
    def test_catches_a_sibling_artifact_in_a_skill(self) -> None:
        """Teeth. Inverted on the port: here the sibling is the equities fork."""
        found = check_parent_leakage(
            [(".claude/skills/foo/SKILL.md", "run `make wifey-backtest`")]
        )
        assert len(found) == 1
        assert "make wifey-backtest" in found[0].detail

    def test_own_artifacts_are_not_leakage(self) -> None:
        """Specificity, and it is why the teeth test above is worth anything.

        After inverting `LEAKAGE_RE` the inherited fixture (`make buibui-*`)
        went green while testing nothing — the regex no longer matched it, so
        the assertion held for the wrong reason. A suite with only a teeth test
        cannot tell "clean" from "blind".
        """
        assert (
            check_parent_leakage(
                [(".claude/skills/foo/SKILL.md", "run `make buibui-backtest`")]
            )
            == []
        )

    def test_exempt_skill_is_silent(self) -> None:
        assert (
            check_parent_leakage(
                [
                    (
                        ".claude/skills/sync-child/SKILL.md",
                        "cd buibui-wifey-wall-street-bot",
                    )
                ]
            )
            == []
        )

    def test_context_docs_are_exempt(self) -> None:
        assert (
            check_parent_leakage(
                [(".claude/context/tools.md", "the old stocks.json path")]
            )
            == []
        )

    def test_scope_stops_at_dot_claude(self) -> None:
        """CLAUDE.md's fork-lineage paragraph is correct history, not drift.

        Widening this check to the top-level docs reproduces the prose-marker
        grep that was built, measured and rejected: it returns four hits that
        are all true statements about the fork's origin.
        """
        assert (
            check_parent_leakage(
                [
                    (
                        "CLAUDE.md",
                        "A fork of `s10023/buibui-moon-trader-bot`, frozen at 635ed5a",
                    )
                ]
            )
            == []
        )


class TestMissingPaths:
    def test_catches_a_path_that_is_simply_gone(self) -> None:
        found = check_missing_paths(
            [("doc.md", "see `analytics/ghost.py` for detail")],
            exists=lambda _p: False,
            ignored=lambda _p: False,
        )
        assert found == [Finding("missing-paths", "doc.md: MISSING analytics/ghost.py")]

    def test_existing_path_is_clean(self) -> None:
        assert (
            check_missing_paths(
                [("doc.md", "`analytics/real.py`")],
                exists=lambda _p: True,
                ignored=lambda _p: False,
            )
            == []
        )

    def test_gitignored_path_is_clean(self) -> None:
        """The leg that makes the check CI-portable.

        `config/coins.json` is absent in a clean checkout **by design**, so a
        hard-coded allowlist calibrated on a developer machine reports a false
        failure in CI. Asking git instead moves the answer to the only place
        that knows it.
        """
        assert (
            check_missing_paths(
                [("doc.md", "`config/coins.json`")],
                exists=lambda _p: False,
                ignored=lambda _p: True,
            )
            == []
        )

    def test_allowlisted_path_is_clean(self) -> None:
        assert (
            check_missing_paths(
                [("doc.md", "`trade/open_trades.py` was removed")],
                exists=lambda _p: False,
                ignored=lambda _p: False,
            )
            == []
        )

    def test_a_path_is_reported_once(self) -> None:
        found = check_missing_paths(
            [("a.md", "`tools/ghost.py`"), ("b.md", "`tools/ghost.py`")],
            exists=lambda _p: False,
            ignored=lambda _p: False,
        )
        assert len(found) == 1


class TestContextCoverage:
    def test_catches_an_undocumented_package(self) -> None:
        assert check_context_coverage(
            ["analytics", "ghostpkg"], "analytics is here"
        ) == [Finding("context-coverage", "UNDOCUMENTED package: ghostpkg/")]

    def test_documented_package_is_clean(self) -> None:
        assert check_context_coverage(["analytics"], "the analytics layer") == []

    def test_exempt_package_is_clean(self) -> None:
        assert check_context_coverage(["deploy"], "") == []

    def test_match_is_word_bounded(self) -> None:
        """A substring hit would report a package documented by an unrelated word.

        A false-positive presence check is worse than none, because it reports
        covered.
        """
        assert check_context_coverage(["web"], "the cobweb module") == [
            Finding("context-coverage", "UNDOCUMENTED package: web/")
        ]


class TestRouterWiring:
    def test_all_three_lists_agreeing_is_clean(self) -> None:
        assert check_router_wiring(["a", "b"], ["a", "b"], ["a", "b"]) == []

    def test_catches_a_router_never_imported(self) -> None:
        found = check_router_wiring(["a", "b"], ["a"], ["a"])
        assert found == [
            Finding("router-wiring", "b: on disk, not imported by main.py")
        ]

    def test_catches_an_imported_router_never_registered(self) -> None:
        """The silent shape: it imports cleanly and serves 404s."""
        found = check_router_wiring(["a", "b"], ["a", "b"], ["a"])
        assert found == [Finding("router-wiring", "b: imported but never registered")]

    def test_catches_a_registration_with_no_module(self) -> None:
        found = check_router_wiring(["a"], ["a", "b"], ["a", "b"])
        assert any("no module on disk" in f.detail for f in found)


class TestConfigStrategies:
    def test_catches_a_key_naming_no_strategy(self) -> None:
        found = check_config_strategies(
            [("cfg.toml", {"strategy_params": {"doji": {}, "gone": {}}})], STRATEGIES
        )
        assert found == [
            Finding(
                "config-strategies",
                "cfg.toml: [strategy_params.gone] is not a strategy",
            )
        ]

    def test_real_keys_are_clean(self) -> None:
        assert (
            check_config_strategies(
                [("cfg.toml", {"strategy_params": {"doji": {}}})], STRATEGIES
            )
            == []
        )

    def test_config_without_the_table_is_clean(self) -> None:
        assert (
            check_config_strategies([("cfg.toml", {"backtest": {}})], STRATEGIES) == []
        )


class TestCliDocumented:
    def test_catches_an_undocumented_subcommand(self) -> None:
        assert check_cli_documented(["backtest", "digest"], "run backtest") == [
            Finding("cli-documented", "`buibui digest` is not mentioned in README.md")
        ]

    def test_documented_subcommands_are_clean(self) -> None:
        assert check_cli_documented(["backtest"], "the `buibui backtest` command") == []


class TestSurfaceFilesComeFromConfig:
    """The mutation control for the repoint.

    Asserting the tuple merely *contains* the right paths passes just as well
    against a surviving hardcoded copy. These assert it is DERIVED.
    """

    def test_sanity_surfaces_matches_the_config_view(self) -> None:
        from tools import agents_config, sanity_checks

        cfg = agents_config.load(Path.cwd())
        assert sanity_checks.sanity_surfaces() == cfg.paths_with_role("sanity")

    def test_config_failure_is_not_swallowed(self, tmp_path: Path) -> None:
        """A missing config must raise, never yield an empty sweep.

        An empty surface list makes the drift sweep vacuously clean — the
        failure this repo has shipped twice.
        """
        from tools import agents_config

        with pytest.raises(agents_config.ConfigError):
            agents_config.load(tmp_path)

    def test_a_config_failure_renders_as_a_finding_not_a_crash(self) -> None:
        """The whole reason the read is deferred to call time.

        An import-time load would crash the runner, and a swallowed one would
        print `0 findings, exit 0` — the SKIP-looks-like-PASS failure this repo
        has shipped twice. It must be neither.
        """
        from tools.agents_config import ConfigError

        def boom() -> None:
            raise ConfigError("docs/agents/surfaces.toml is missing")

        results = gather(load_config=boom)
        config_leg = [r for r in results if r.name == "agents-config"]
        assert len(config_leg) == 1
        assert config_leg[0].findings, "a missing config must FIRE, not skip"
        assert config_leg[0].skipped is None

    def test_the_accessor_is_derived_not_literal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mutate the config the module reads; the accessor must move with it.

        A surviving hardcoded tuple passes every other test in this class and
        fails this one — it is the mutation control the other tests are not.
        """
        from tools import agents_config, sanity_checks

        real = agents_config.load(Path.cwd())
        shrunk = dataclasses.replace(real, surfaces=real.surfaces[:1])
        monkeypatch.setattr(sanity_checks, "load_agents_config", lambda: shrunk)
        sanity_checks._cfg.cache_clear()
        try:
            assert len(sanity_checks.sanity_surfaces()) < len(
                real.paths_with_role("sanity")
            )
        finally:
            sanity_checks._cfg.cache_clear()

    def test_importing_the_module_does_not_read_the_config(
        self, tmp_path: Path
    ) -> None:
        """The deferred read, mutation-tested.

        A module-level read crashes on import when no config is present, and
        this module gates CI — so that crash replaces a readable finding with a
        traceback. Running the import from a config-less directory is the only
        way to tell deferred from eager; every in-process test passes against
        both.
        """
        result = subprocess.run(
            [sys.executable, "-c", "import tools.sanity_checks"],
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(Path.cwd())},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"importing the module read the config eagerly: {result.stderr}"
        )


class TestHelpers:
    def test_makefile_targets_reads_target_names(self) -> None:
        assert makefile_targets("test:\n\tpytest\nlint-py: fmt\n") == {
            "test",
            "lint-py",
        }

    def test_subcommand_names_reads_the_argparse_tree(self) -> None:
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="cmd")
        subs.add_parser("alpha")
        subs.add_parser("beta")
        assert subcommand_names(parser) == {"alpha", "beta"}

    def test_surface_paths_excludes_the_self_referential_skills(self) -> None:
        """Those two files quote the anti-patterns in order to hunt for them."""
        names = [str(p) for p in surface_paths()]
        assert not any(n.endswith("sanity-check/SKILL.md") for n in names)
        assert not any(n.endswith("post-branch/SKILL.md") for n in names)
        assert "CLAUDE.md" in names

    def test_render_counts_findings_but_not_notes(self) -> None:
        """A degraded leg must not make the sweep permanently red."""
        lines, total = render(
            [CheckResult("fork-drift", [], note="symbol leg skipped")]
        )
        assert total == 0
        assert any("note: symbol leg skipped" in line for line in lines)

    def test_render_counts_a_real_finding(self) -> None:
        _lines, total = render([CheckResult("x", [Finding("x", "bad")])])
        assert total == 1


@pytest.mark.skipif(
    not Path("CLAUDE.md").exists(), reason="not running from the repo root"
)
def test_working_tree_is_clean() -> None:
    """The gate: doc drift fails the suite, and therefore CI.

    This is the whole point of extracting the skill's shell blocks. A script
    that only runs when a human remembers eventually reports failure to nobody.
    """
    results = gather()
    findings = [f.detail for r in results for f in r.findings]
    assert findings == []


def test_runs_as_a_bare_script_with_no_pythonpath() -> None:
    """CI invokes this as `python3 tools/sanity_checks.py` with NO PYTHONPATH.

    The Make target sets `PYTHONPATH=.`, so a green `make sanity-checks` says
    nothing about the invocation CI actually uses. That divergence shipped a red
    CI on a branch whose every local gate was green: adding a `tools.*` import to
    a script that had none put `tools/` on sys.path instead of the repo root, and
    the step failed on an import rather than on a finding.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "tools/sanity_checks.py"],
        cwd=Path(__file__).resolve().parent.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert "ModuleNotFoundError" not in result.stderr, result.stderr
    assert "Traceback" not in result.stderr, result.stderr
