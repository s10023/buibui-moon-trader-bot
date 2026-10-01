"""Tests for `tools/distil_power.py` — the G3 gate's CLI."""

from __future__ import annotations

import importlib.util
import math
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "tools" / "distil_power.py"
    spec = importlib.util.spec_from_file_location("distil_power", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["distil_power"] = mod
    spec.loader.exec_module(mod)
    return mod


distil_power = _load()


def test_units_is_mandatory() -> None:
    """An undeclared unit is how a portable-looking number changes meaning."""
    with pytest.raises(SystemExit):
        distil_power.main(
            [
                "--sr-footing",
                "per_obs",
                "--n-obs",
                "1000",
                "--n-trials",
                "16",
                "--sr-variance",
                "0.05",
            ]
        )


def test_benign_family_is_reachable(capsys: pytest.CaptureFixture[str]) -> None:
    code = distil_power.main(
        [
            "--units",
            "per_trade",
            "--sr-footing",
            "per_obs",
            "--n-obs",
            "20000",
            "--n-trials",
            "1",
            "--sr-variance",
            "0.0",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "REACHABLE" in out
    assert "UNREACHABLE" not in out


def test_hostile_family_is_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    """A tiny sample against a wide trial family cannot clear the gate at any effect size.

    n_obs=2 is the ONLY value that works here, and it was measured rather than
    reasoned: the PSR z-statistic is bounded above by ``sqrt(2*(n_obs-1))``, so
    against ``Z_GATE(0.95) = 1.6449`` n=2 gives z_max 1.4142 (unreachable) but
    n=3 gives 2.0000 and a finite required Sharpe of 4.6463. Do not raise this
    number to make the test "more realistic" — it stops testing anything.
    """
    code = distil_power.main(
        [
            "--units",
            "per_trade",
            "--sr-footing",
            "per_obs",
            "--n-obs",
            "2",
            "--n-trials",
            "320",
            "--sr-variance",
            "0.05",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "UNREACHABLE" in out


def test_correlation_deflator_raises_the_bar(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pooling correlated series must make the bar HARDER, never easier."""
    base = distil_power.price(
        distil_power.parse_args(
            [
                "--units",
                "per_alert",
                "--sr-footing",
                "per_obs",
                "--n-obs",
                "4000",
                "--n-trials",
                "16",
                "--sr-variance",
                "0.05",
            ]
        )
    )
    deflated = distil_power.price(
        distil_power.parse_args(
            [
                "--units",
                "per_alert",
                "--sr-footing",
                "per_obs",
                "--n-obs",
                "4000",
                "--n-trials",
                "16",
                "--sr-variance",
                "0.05",
                "--n-series",
                "25",
                "--n-eff",
                "2.92",
            ]
        )
    )
    # "effective n" appears in BOTH branches, so asserting on it tests nothing.
    # "deflated by" is printed only when the deflator actually applied.
    assert "deflated by" in "\n".join(deflated)
    assert "no deflator applied" in "\n".join(base)
    assert "\n".join(base) != "\n".join(deflated)

    # The label check above would still pass if the deflator made the bar
    # EASIER — the exact failure class this tool exists to prevent. Assert the
    # numeric direction too, so a magnitude regression fails this test.
    def _required_sharpe(lines: list[str]) -> float:
        (line,) = [ln for ln in lines if "required Sharpe" in ln]
        return float(line.split()[-1])

    assert _required_sharpe(deflated) > _required_sharpe(base)


def test_effect_size_reported_when_sd_given(capsys: pytest.CaptureFixture[str]) -> None:
    code = distil_power.main(
        [
            "--units",
            "per_trade",
            "--sr-footing",
            "per_obs",
            "--n-obs",
            "4000",
            "--n-trials",
            "16",
            "--sr-variance",
            "0.05",
            "--sd",
            "1.2",
            "--corpus-best",
            "1.196",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "required effect" in out
    assert "corpus best" in out


def test_null_containment_uses_the_owning_predicate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--bar must report a containment verdict, never an |delta| < MDE comparison."""
    code = distil_power.main(
        [
            "--units",
            "per_alert",
            "--sr-footing",
            "per_obs",
            "--n-obs",
            "4000",
            "--n-trials",
            "1",
            "--sr-variance",
            "0.0",
            "--sd",
            "1.0",
            "--bar",
            "0.05",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "powered null" in out.lower()


def test_effective_n_rejects_n_eff_exceeding_n_series() -> None:
    """A deflator cannot claim more independent series than series exist.

    Reviewer-found regression: reversed ``--n-series``/``--n-eff`` (3 series,
    25 "effective" ones) inflated n_used and LOWERED the required Sharpe — the
    bar got easier, the exact failure class this tool exists to prevent.
    """
    with pytest.raises(ValueError, match=r"n_eff .* cannot exceed n_series"):
        distil_power.effective_n(4000, 3, 25.0)


def test_reversed_deflator_args_rejected_via_cli(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The exact reviewer-reported case: --n-series 3 --n-eff 25 must not silently price."""
    code = distil_power.main(
        [
            "--units",
            "per_alert",
            "--sr-footing",
            "per_obs",
            "--n-obs",
            "4000",
            "--n-trials",
            "16",
            "--sr-variance",
            "0.05",
            "--n-series",
            "3",
            "--n-eff",
            "25",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "cannot exceed" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_single_flag_deflator_error_exits_cleanly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--n-series with no --n-eff (or vice versa) must exit 2, not raise a traceback."""
    code = distil_power.main(
        [
            "--units",
            "per_alert",
            "--sr-footing",
            "per_obs",
            "--n-obs",
            "4000",
            "--n-trials",
            "16",
            "--sr-variance",
            "0.05",
            "--n-series",
            "25",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "supplied together" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_runs_as_a_bare_script_with_no_pythonpath() -> None:
    """ST59. `/research-distil`'s G3 gate REQUIRES running this tool and forbids
    estimating, so the one tool a session is obliged to run must not die on the
    obvious invocation. A bare `python3 tools/distil_power.py` puts `tools/` on
    sys.path rather than the repo root, and the `analytics.*` imports then raise
    ModuleNotFoundError; the Make target sets `PYTHONPATH=.` and hides it. A
    session that hits the traceback and estimates instead has silently defeated
    the gate. Its two siblings self-bootstrap for the same reason.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [
            sys.executable,
            "tools/distil_power.py",
            "--units",
            "per_trade",
            "--sr-footing",
            "per_obs",
            "--n-obs",
            "1000",
            "--n-trials",
            "16",
            "--sr-variance",
            "0.05",
        ],
        cwd=Path(__file__).resolve().parent.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert "ModuleNotFoundError" not in result.stderr, result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stderr


# --- ST76: `--corpus-best` without `--sd` ------------------------------------
#
# The comparison is only defined when `--sd` converts the required Sharpe into
# effect units. Before ST76 the missing-`--sd` case fell through to the same
# bare "VERDICT REACHABLE" line as a genuine pass, so a run that never compared
# anything was indistinguishable from one that cleared the corpus best -- on a
# tool the G3 gate MANDATES running.

_BARE_REACHABLE = "  VERDICT           REACHABLE"


def _verdict_lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if "VERDICT" in ln]


def test_corpus_best_without_sd_never_reads_as_a_bare_reachable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """did-not-compare must not render as passed."""
    code = distil_power.main(
        [
            "--units",
            "per_trade",
            "--sr-footing",
            "per_obs",
            "--n-obs",
            "4000",
            "--n-trials",
            "16",
            "--sr-variance",
            "0.05",
            "--corpus-best",
            "1.196",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    # The corpus best is still echoed -- it was passed, so it is reported.
    assert "corpus best" in out
    # ...but the verdict must NOT be the bare pass line.
    assert _verdict_lines(out) == [
        "  VERDICT           REACHABLE, but the corpus best was NOT COMPARED"
    ]
    assert _BARE_REACHABLE not in out.splitlines()
    # It must name the missing input, not merely hedge.
    assert "--sd" in out


def test_corpus_best_with_sd_still_reads_bare_reachable_when_cleared(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The both-flags 'bar is within reach' verdict is unchanged by ST76."""
    code = distil_power.main(
        [
            "--units",
            "per_trade",
            "--sr-footing",
            "per_obs",
            "--n-obs",
            "40000",
            "--n-trials",
            "1",
            "--sr-variance",
            "0.01",
            "--sd",
            "1.2",
            "--corpus-best",
            "1.196",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert _verdict_lines(out) == [_BARE_REACHABLE]


def test_corpus_best_with_sd_still_flags_a_bar_exceeding_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The both-flags 'bar EXCEEDS the corpus best' verdict is unchanged by ST76."""
    code = distil_power.main(
        [
            "--units",
            "per_trade",
            "--sr-footing",
            "per_obs",
            "--n-obs",
            "4000",
            "--n-trials",
            "320",
            "--sr-variance",
            "0.5",
            "--sd",
            "1.2",
            "--corpus-best",
            "0.001",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert _verdict_lines(out) == [
        "  VERDICT           REACHABLE, but the bar EXCEEDS the corpus best"
    ]


# --- #857: Sharpe footing -----------------------------------------------------
#
# PSR runs per OBSERVATION. An annualized `--sr-variance` / `--corpus-best` /
# `--bar` beside an `--n-obs` counted in days prices the bar in the wrong units
# and still prints a clean verdict, so the footing is now declared, converted
# under `annual`, and refused under `per_obs` when it implies an implausible
# annualized Sharpe.

# The sibling equities fork's filed case (H-023 there): annualized sleeve inputs
# beside n_obs in NYSE days. Its 252 is THAT fork's year and appears here only as
# that case's input; this repo annualizes at 365.
_SIBLING_H023 = [
    "--units",
    "per_book_day",
    "--n-obs",
    "2174",
    "--n-trials",
    "4",
]

# This repo's own anchors (AGENTS.md "Sleeve verdicts"): the seven filed sleeve
# Sharpes are annualized at 365, their sample variance is 1.9661576, and the
# deploy core has ~2475 book days.
_FILED_FAMILY_VAR_ANNUAL = 1.9661576
_FILED_FAMILY_VAR_PER_DAY = _FILED_FAMILY_VAR_ANNUAL / 365.0
_DEPLOY_CORE_ANNUAL = 1.375
_REPO_BOOK = [
    "--units",
    "per_book_day",
    "--n-obs",
    "2475",
    "--n-trials",
    "4",
]


def _required_sharpe_fields(out: str) -> list[float]:
    (line,) = [ln for ln in out.splitlines() if "required Sharpe" in ln]
    return [float(tok.strip("()")) for tok in line.split()[2:] if tok[-1].isdigit()]


def test_footing_is_mandatory() -> None:
    with pytest.raises(SystemExit):
        distil_power.main([*_REPO_BOOK, "--sr-variance", "0.005"])


def test_book_day_year_matches_the_sleeves() -> None:
    """The plausibility year is the sleeves' annualization, not a second dial."""
    from analytics.forecast.config import ForecastConfig

    assert ForecastConfig().annualization_days == distil_power.BOOK_DAYS_PER_YEAR


def test_mixed_footing_reproduces_the_sibling_forks_filed_bar() -> None:
    """Positive control: the wrong-footing arithmetic the sibling fork filed."""
    from analytics.research_guards import required_sharpe

    sr = required_sharpe(2174, n_trials=4, sr_variance=0.0652)
    assert sr == pytest.approx(0.3047, abs=5e-5)
    assert sr * math.sqrt(252) == pytest.approx(4.84, abs=5e-3)


def test_annual_footing_converts_to_the_repriced_bar(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The sibling case declared honestly: 0.83 annual, ABOVE its 0.41 corpus best."""
    code = distil_power.main(
        [
            *_SIBLING_H023,
            "--sr-footing",
            "annual",
            "--periods-per-year",
            "252",
            "--sr-variance",
            "0.0652",
            "--corpus-best",
            "0.41",
            "--bar",
            "0.70",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Sharpe footing    annual, converted at 252 periods/year" in out
    annual, _per_obs = _required_sharpe_fields(out)
    assert annual == pytest.approx(0.83, abs=5e-3)
    assert _verdict_lines(out) == [
        "  VERDICT           REACHABLE, but the bar EXCEEDS the corpus best"
    ]
    (half_line,) = [ln for ln in out.splitlines() if "CI half-width" in ln]
    assert float(half_line.split()[2]) == pytest.approx(0.667, abs=5e-4)


def test_both_footings_price_the_same_bar() -> None:
    """annual with variance V at P == per_obs with V / P, times sqrt(P)."""
    annual = distil_power.price(
        distil_power.parse_args(
            [
                *_REPO_BOOK,
                "--sr-footing",
                "annual",
                "--periods-per-year",
                "365",
                "--sr-variance",
                str(_FILED_FAMILY_VAR_ANNUAL),
            ]
        )
    )
    per_obs = distil_power.price(
        distil_power.parse_args(
            [
                *_REPO_BOOK,
                "--sr-footing",
                "per_obs",
                "--periods-per-year",
                "365",
                "--sr-variance",
                str(_FILED_FAMILY_VAR_PER_DAY),
            ]
        )
    )
    sr_annual, sr_annual_per_obs = _required_sharpe_fields("\n".join(annual))
    (sr_per_obs,) = _required_sharpe_fields("\n".join(per_obs))
    assert sr_annual_per_obs == pytest.approx(sr_per_obs, rel=1e-5)
    assert sr_annual == pytest.approx(sr_per_obs * math.sqrt(365), rel=1e-5)
    (echo,) = [ln for ln in per_obs if "annualized at 365" in ln]
    assert float(echo.split()[1]) == pytest.approx(sr_annual, rel=1e-5)


@pytest.mark.parametrize(
    "variance",
    [
        pytest.param("0.0652", id="sibling-fork-H023"),
        pytest.param(str(_FILED_FAMILY_VAR_ANNUAL), id="this-repo-filed-family"),
    ],
)
def test_annualized_variance_declared_per_obs_is_refused(
    capsys: pytest.CaptureFixture[str], variance: str
) -> None:
    code = distil_power.main(
        [*_REPO_BOOK, "--sr-footing", "per_obs", "--sr-variance", variance]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "--sr-footing annual --periods-per-year 365" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_annualized_corpus_best_declared_per_obs_is_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The deploy core's annualized +1.375 passed as a per-day Sharpe implies 26.3."""
    code = distil_power.main(
        [
            *_REPO_BOOK,
            "--sr-footing",
            "per_obs",
            "--sr-variance",
            str(_FILED_FAMILY_VAR_PER_DAY),
            "--sd",
            "1.0",
            "--corpus-best",
            str(_DEPLOY_CORE_ANNUAL),
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "--corpus-best" in captured.err
    assert "--sr-footing annual --periods-per-year 365" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    "corpus_best_annual",
    [
        pytest.param(_DEPLOY_CORE_ANNUAL, id="deploy-core"),
        # The largest filed |Sharpe|; the ceiling must leave it headroom.
        pytest.param(-2.9, id="xsrev"),
    ],
)
def test_per_obs_book_day_inputs_still_price(
    capsys: pytest.CaptureFixture[str], corpus_best_annual: float
) -> None:
    """Positive control for both refusals: honest per-day inputs still price."""
    code = distil_power.main(
        [
            *_REPO_BOOK,
            "--sr-footing",
            "per_obs",
            "--sr-variance",
            str(_FILED_FAMILY_VAR_PER_DAY),
            "--sd",
            "1.0",
            "--corpus-best",
            f"{corpus_best_annual / math.sqrt(365):.6f}",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "VERDICT" in captured.out


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        pytest.param([], "needs --periods-per-year", id="missing-periods"),
        pytest.param(
            ["--periods-per-year", "365", "--sd", "1.0"],
            "--sd does not apply",
            id="sd-given",
        ),
        pytest.param(["--periods-per-year", "0"], "must be > 0", id="zero-periods"),
    ],
)
def test_annual_footing_declared_errors_exit_cleanly(
    capsys: pytest.CaptureFixture[str], extra: list[str], message: str
) -> None:
    code = distil_power.main(
        [
            *_REPO_BOOK,
            "--sr-footing",
            "annual",
            "--sr-variance",
            str(_FILED_FAMILY_VAR_ANNUAL),
            *extra,
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert message in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
