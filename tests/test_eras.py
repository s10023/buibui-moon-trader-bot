"""Tests for analytics.eras.

The load-bearing property is NOT "it finds boundaries" — it is that it never
reports a sample as clean because it failed to look. Several tests below assert
the raise, and the straddle tests are paired positive/negative controls so a pass
proves the boundary is what fired the warning.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from analytics.eras import (
    BACKTEST_PATHS,
    DEFAULT_ERAS_PATH,
    SCOPES,
    EraBoundary,
    declared_boundaries,
    git_boundaries,
    load_boundaries,
    split_by_era,
    straddle_report,
    straddled,
)


def _ms(day: str) -> int:
    return int(
        datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000
    )


def _boundary(day: str, label: str = "b", scope: str = "backtest") -> EraBoundary:
    return EraBoundary(
        ts_ms=_ms(day), label=label, scope=scope, source="declared", ref=label
    )


# --- git derivation --------------------------------------------------------


def _fake_log(*records: tuple[str, str, str, list[str]]) -> str:
    """Build git output in the exact shape `git_boundaries` asks git for."""
    out = []
    for day, sha, subject, files in records:
        epoch = _ms(day) // 1000
        out.append(f"\x1e{epoch}\t{sha}\t{subject}\n" + "\n".join(files) + "\n")
    return "".join(out)


def test_the_format_string_carries_no_byte_argv_cannot_hold() -> None:
    """Regression: a literal NUL separator raised `embedded null byte` at exec."""
    seen: dict[str, Any] = {}

    def runner(args: Sequence[str], *, cwd: Path) -> str:
        seen["args"] = list(args)
        return ""

    git_boundaries(runner=runner)

    for arg in seen["args"]:
        assert "\x00" not in arg, f"argv entry {arg!r} holds a NUL"


def test_git_boundaries_parses_log_into_sorted_boundaries() -> None:
    payload = _fake_log(
        ("2026-06-04", "63bfa43", "prune pin_bar 1h", ["config/signal_watch.toml"]),
        ("2026-05-21", "23ef8df", "deprecate volume_spike_boost", ["config/x.toml"]),
    )

    def runner(args: Sequence[str], *, cwd: Path) -> str:
        return payload

    found = git_boundaries(runner=runner)

    assert [b.ref for b in found] == ["23ef8df", "63bfa43"]  # sorted oldest first
    assert found[1].label == "prune pin_bar 1h"
    assert found[1].date == "2026-06-04"
    assert found[1].scope == "backtest"
    assert found[1].source == "git"
    assert "config/signal_watch.toml" in found[1].why


def test_git_boundaries_asks_git_for_the_behaviour_paths() -> None:
    seen: dict[str, Any] = {}

    def runner(args: Sequence[str], *, cwd: Path) -> str:
        seen["args"] = list(args)
        return ""

    git_boundaries(runner=runner)

    assert seen["args"][0] == "log"
    assert "--name-only" in seen["args"]
    for path in BACKTEST_PATHS:
        assert path in seen["args"], f"{path} was not queried"


def test_docs_and_test_commits_are_not_boundaries() -> None:
    """A `docs:` commit recording a verdict edits analytics/strategies/ but changes nothing."""
    payload = _fake_log(
        (
            "2026-05-11",
            "aaa1111",
            "docs(strategies): ote_entry WFO no-edge audit",
            ["a"],
        ),
        ("2026-05-12", "bbb2222", "test(backtest): widen the fixture", ["b"]),
        ("2026-05-13", "ccc3333", "feat(config): flip bos regime mapping", ["c"]),
    )

    found = git_boundaries(runner=lambda args, *, cwd: payload)

    assert [b.ref for b in found] == ["ccc3333"]


@pytest.mark.parametrize("kind", ["chore", "build", "refactor", "fix", "feat", "perf"])
def test_types_that_can_move_behaviour_stay_boundaries(kind: str) -> None:
    """Pins the OMISSIONS from NON_BEHAVIOUR_TYPES — dropping one must fail here.

    `chore`/`build`/`refactor` look inert and are not: a dep bump, a config tidy and
    a file move have each moved goldens in this repo.
    """
    payload = _fake_log(("2026-05-13", "ddd4444", f"{kind}(x): something", ["c"]))

    found = git_boundaries(runner=lambda args, *, cwd: payload)

    assert [b.ref for b in found] == ["ddd4444"], f"{kind} must remain a boundary"


def test_git_boundaries_rejects_an_unknown_scope() -> None:
    with pytest.raises(ValueError, match="unknown scope"):
        git_boundaries(scope="nonsense", runner=lambda args, *, cwd: "")


def test_a_git_failure_propagates_rather_than_reporting_no_boundaries() -> None:
    """The whole point: an empty result must never come from our own failure."""

    def broken(args: Sequence[str], *, cwd: Path) -> str:
        raise RuntimeError("git exited 128: not a repository")

    with pytest.raises(RuntimeError, match="not a repository"):
        load_boundaries(runner=broken)


# --- declared registry -----------------------------------------------------


def test_missing_registry_raises_instead_of_returning_empty(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="straddling nothing"):
        declared_boundaries(tmp_path / "absent.toml")


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ('[[boundary]]\nid="a"\ndate="2026-01-01"\nscope="backtest"\nlabel="l"', "why"),
        (
            '[[boundary]]\nid="a"\ndate="2026-01-01"\nscope="nope"\nlabel="l"\nwhy="w"',
            "unknown scope",
        ),
        (
            '[[boundary]]\nid="a"\ndate="13-08-2026"\nscope="ratings"\nlabel="l"\nwhy="w"',
            "unparseable date",
        ),
        ("other = 1", "declares no"),
    ],
)
def test_malformed_registry_entries_raise(
    tmp_path: Path, body: str, match: str
) -> None:
    path = tmp_path / "eras.toml"
    path.write_text(body)
    with pytest.raises(ValueError, match=match):
        declared_boundaries(path)


def test_the_committed_registry_is_valid_and_every_entry_is_explained() -> None:
    """Guards the real file, so a bad edit fails here rather than at review time."""
    found = declared_boundaries(DEFAULT_ERAS_PATH)

    assert found, "config/eras.toml declares nothing"
    for boundary in found:
        assert boundary.scope in SCOPES
        assert boundary.source == "declared"
        assert len(boundary.why.strip()) > 40, f"{boundary.ref} has no real rationale"
        assert boundary.ts_ms > 0
    assert found == sorted(found)


# --- straddle detection ----------------------------------------------------


def test_straddled_is_strict_at_both_ends() -> None:
    edge = _boundary("2026-06-01")
    inside = _boundary("2026-06-15")
    pool = [edge, inside]

    crossed = straddled(pool, _ms("2026-06-01"), _ms("2026-07-01"))

    assert crossed == [inside], "a boundary AT the window start opens it, not splits it"
    assert straddled(pool, _ms("2026-06-01"), _ms("2026-06-15")) == []


def test_straddled_rejects_an_inverted_window() -> None:
    with pytest.raises(ValueError, match="postdates"):
        straddled([], _ms("2026-07-01"), _ms("2026-06-01"))


def test_split_by_era_places_observations_including_pre_boundary_ones() -> None:
    boundaries = [_boundary("2026-06-01", "first"), _boundary("2026-07-01", "second")]
    obs = [
        _ms("2026-05-10"),
        _ms("2026-06-10"),
        _ms("2026-06-20"),
        _ms("2026-08-10"),
    ]

    spans = split_by_era(boundaries, obs)

    assert [(s.opened_by.label if s.opened_by else None, s.n_obs) for s in spans] == [
        (None, 1),
        ("first", 2),
        ("second", 1),
    ]


def test_split_by_era_omits_eras_the_sample_does_not_cover() -> None:
    boundaries = [_boundary("2026-06-01", "used"), _boundary("2026-09-01", "unused")]

    spans = split_by_era(boundaries, [_ms("2026-06-10")])

    assert len(spans) == 1
    assert spans[0].opened_by is not None
    assert spans[0].opened_by.label == "used"


# --- the report: paired controls -------------------------------------------


def test_report_says_clean_when_no_boundary_falls_inside() -> None:
    outside = _boundary("2026-01-01", "long before")

    lines = straddle_report([outside], [_ms("2026-06-10"), _ms("2026-06-20")])

    assert any("CLEAN" in line for line in lines)
    assert not any("STRADDLES" in line for line in lines)


def test_report_warns_and_names_the_largest_clean_sub_sample() -> None:
    """Positive control for the CLEAN test above — same sample, boundary moved in."""
    inside = _boundary("2026-06-15", "the prune")
    obs = [_ms("2026-06-10"), _ms("2026-06-20"), _ms("2026-06-21")]

    lines = straddle_report([inside], obs)
    blob = "\n".join(lines)

    assert "STRADDLES 1 boundaries" in blob
    assert "the prune" in blob
    assert "n=2 (67%)" in blob, "must name the largest single-era sub-sample"


def test_report_handles_an_empty_sample_without_claiming_cleanliness() -> None:
    lines = straddle_report([_boundary("2026-06-15")], [])

    assert not any("CLEAN" in line for line in lines)
    assert any("no observations" in line for line in lines)


def test_report_truncates_a_long_boundary_list() -> None:
    many = [_boundary(f"2026-06-{d:02d}", f"b{d}") for d in range(2, 20)]

    lines = straddle_report(many, [_ms("2026-06-01"), _ms("2026-06-25")], max_listed=3)

    assert sum(1 for line in lines if line.strip().startswith("·")) == 4  # 3 + "more"
    assert any("and 15 more" in line for line in lines)
