"""ST128 — the WFO CLI must be able to express every shipped config's day_filter.

`param-sweep` and `param-audit` are what AGENTS.md calls the trusted production
source of `tp_r`. Both validated `--day-filter` against a restated
`["off", "weekdays", "tue_thu"]` while `_day_filter_to_weekdays` — which they
delegate to at `param_sweep.py:426` and `:833` — understands six modes, two of
which are exactly what two of the three live configs declare. So `mon_fri` and
`weekend` were argparse errors, and NO available choice isolates either
population: `weekdays` and `tue_thu` both contain zero weekend days, and
`weekdays` carries Mon and Fri plus three days that config never runs on.

⚠ The defect is not the narrow list on its own — it is that the list was
RESTATED away from the function that interprets it. An unknown mode resolves to
None, which means "no filter", so the two halves fail in opposite silent
directions: a mode the CLI refuses is unreachable, and a typo the CLI accepts
sweeps every day. Pinning the vocabulary to the SHIPPED CONFIGS rather than to a
literal is what makes this test see the next config someone adds.
"""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

import pytest

import cli.param as param_cli
from analytics.signal_config import DAY_FILTER_MODES, _day_filter_to_weekdays

_CONFIG_GLOB = "config/signal_watch*.toml"


def _shipped_day_filters() -> dict[str, str]:
    """`{config path: declared day_filter}` for every shipped signal_watch TOML."""
    out: dict[str, str] = {}
    for path in sorted(Path().glob(_CONFIG_GLOB)):
        with path.open("rb") as fh:
            declared = tomllib.load(fh).get("day_filter")
        # A config with no day_filter would silently drop out of the sweep below
        # and the whole guard would pass on an empty set.
        assert declared is not None, f"{path} declares no top-level day_filter"
        out[str(path)] = declared
    return out


def _build_parser() -> argparse.ArgumentParser:
    """The two WFO subparsers, wired the way `cli/main.py` wires them."""
    parser = argparse.ArgumentParser(prog="buibui")
    subparsers = parser.add_subparsers(dest="command")
    param_cli.add_param_sweep_subparser(subparsers)
    param_cli.add_param_audit_subparser(subparsers)
    return parser


_MIN_SWEEP = ["--strategy", "engulfing", "--symbol", "BTCUSDT", "--timeframe", "4h"]
_MIN_AUDIT = ["--symbol", "BTCUSDT", "--timeframe", "4h"]


class TestEveryShippedConfigIsSweepable:
    """The regression: both WFO halves must accept every live config's population."""

    def test_the_fixture_actually_found_the_configs(self) -> None:
        shipped = _shipped_day_filters()
        assert len(shipped) >= 3, f"expected the 3 live configs, got {shipped}"
        # The two that were unreachable are the point of this file; if a rename
        # ever drops them the guard must fail rather than quietly narrow.
        assert {"mon_fri", "weekend"} <= set(shipped.values()), shipped

    @pytest.mark.parametrize(
        "subcommand,minimal", [("param-sweep", _MIN_SWEEP), ("param-audit", _MIN_AUDIT)]
    )
    def test_every_shipped_day_filter_parses(
        self, subcommand: str, minimal: list[str]
    ) -> None:
        parser = _build_parser()
        for config, declared in _shipped_day_filters().items():
            args = parser.parse_args([subcommand, *minimal, "--day-filter", declared])
            assert args.day_filter == declared, f"{config} via {subcommand}"


class TestVocabularyMatchesTheInterpreter:
    """The tuple and `_day_filter_to_weekdays` are one vocabulary, not two."""

    @pytest.mark.parametrize("mode", [m for m in DAY_FILTER_MODES if m != "off"])
    def test_every_offered_mode_resolves_to_real_weekdays(self, mode: str) -> None:
        days = _day_filter_to_weekdays(mode)
        assert days, f"CLI offers {mode!r} but the interpreter does not know it"

    def test_off_is_the_only_no_filter_mode(self) -> None:
        assert _day_filter_to_weekdays("off") is None

    def test_an_unknown_mode_means_no_filter_so_the_cli_must_reject_it(self) -> None:
        # Documents WHY the choices list has to exist at all: the interpreter
        # answers a typo with "sweep every day" rather than with an error.
        assert _day_filter_to_weekdays("mon-fri") is None
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["param-sweep", *_MIN_SWEEP, "--day-filter", "mon-fri"])


class TestScopedNotBlanket:
    """Mutation: the guard must FAIL when the vocabulary narrows again."""

    def test_dropping_a_mode_breaks_the_shipped_config_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        narrowed = tuple(m for m in DAY_FILTER_MODES if m not in {"mon_fri", "weekend"})
        monkeypatch.setattr(param_cli, "DAY_FILTER_MODES", narrowed)
        parser = _build_parser()
        for unreachable in ("mon_fri", "weekend"):
            with pytest.raises(SystemExit):
                parser.parse_args(
                    ["param-sweep", *_MIN_SWEEP, "--day-filter", unreachable]
                )
