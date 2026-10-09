"""Price a candidate hypothesis BEFORE it is written into the inbox.

The G3 gate of ``/research-distil``. Prints the effect size the gate demands at
the declared ``n`` and trial family, beside the corpus best, so a claim is
*priced* rather than estimated.

Two things this exists to prevent, both with filed precedent in this repo:

* **A power claim the model did the arithmetic for.** ST28's spec and its driver
  disagreed on the detection threshold while both halves stayed internally
  consistent, so no gate, grep or review surface caught it. Running one tracked
  tool is the only structural defence.
* **A number whose units are implied.** ``--units`` is mandatory and has no
  default. The H15 ``bar``-units trap and the 25-symbol 2.92x deflator reused on
  panels whose true deflator is 1.628x or 3.331x are the same defect: a figure
  that looks portable and silently changes meaning with the panel.
* **A Sharpe whose footing is implied.** ``--sr-footing`` is mandatory too.
  PSR runs per OBSERVATION, so an annualized ``--sr-variance`` or
  ``--corpus-best`` beside an ``--n-obs`` counted in days prices a bar in the
  wrong units and still prints a clean verdict. The sibling equities fork
  shipped exactly that: annualized sleeve Sharpes beside ``n_obs`` in days
  priced a per-day bar of 0.3047, compared it to an annualized corpus best and
  printed REACHABLE, where the honest bar was 0.83 annualized, twice its corpus
  best. ``tests/test_distil_power.py`` keeps that case as the positive control.

The null-containment verdict is delegated to
:func:`analytics.audit_guard.powered_null`. The comparison is never restated here.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path

# Runnable as a bare script, not only through the Make target. `/research-distil`'s
# G3 gate MANDATES running this tool and forbids estimating it, so the one tool a
# session is obliged to run must not die on the obvious invocation: a bare
# `python3 tools/distil_power.py` puts `tools/` on sys.path rather than the repo
# root, and the `analytics.*` imports below then raise ModuleNotFoundError. The Make
# target sets PYTHONPATH=., so a green target says nothing about the bare form, and
# a session that hits the traceback and estimates instead has defeated the gate
# silently. `sanity_checks.py:52` and `post_branch_checks.py:45` do the same.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.audit_guard import powered_null  # noqa: E402
from analytics.research_guards import GATE_DSR, required_sharpe  # noqa: E402

Z_95 = 1.959963984540054

UNITS = ("per_trade", "per_alert", "per_book_day")
FOOTINGS = ("per_obs", "annual")

# Every sleeve here annualizes at `ForecastConfig.annualization_days` = 365:
# crypto trades every day, so a book-day year is NOT the equities 252. Restated
# rather than imported because importing `analytics.forecast` pulls pandas and
# duckdb into a pure-math CLI; `test_book_day_year_matches_the_sleeves` pins the
# two together so they cannot drift.
BOOK_DAYS_PER_YEAR = 365.0

# A per_obs declaration on `per_book_day` is refused when it implies an
# annualized Sharpe, or trial-family Sharpe dispersion, above this. Derived from
# THIS repo's filed sleeves (AGENTS.md "Sleeve verdicts"): the largest |Sharpe| is
# xsrev's -2.9, and the seven-sleeve family's dispersion is sd 1.40. The
# mixed-footing inputs it exists to catch land far above it: the deploy core's
# annualized +1.375 read as a per-day Sharpe implies 26.3, and the family's
# variance 1.966 read per day implies 26.8. The sibling fork's 3.0 left xsrev 3%
# of headroom, and a sleeve negative at ZERO cost only grows in magnitude once
# costed, so 4.0 buys margin on the honest side and still sits 6.6x below the
# deploy-core misread.
# ⚠ The price: an annualized input under 4 / sqrt(365) = 0.209 declared per_obs is
# NOT caught (carry's +0.03, cvd's -0.147 / +0.183). This is a plausibility
# ceiling, never a proof of footing.
MAX_PLAUSIBLE_ANNUAL_SHARPE = 4.0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="distil_power",
        description="Price a hypothesis against the gate before designing it.",
    )
    parser.add_argument("--units", required=True, choices=UNITS)
    parser.add_argument("--sr-footing", required=True, choices=FOOTINGS)
    parser.add_argument("--periods-per-year", type=float, default=None)
    parser.add_argument("--n-obs", type=int, required=True)
    parser.add_argument("--n-trials", type=int, required=True)
    parser.add_argument("--sr-variance", type=float, required=True)
    parser.add_argument("--n-series", type=int, default=None)
    parser.add_argument("--n-eff", type=float, default=None)
    parser.add_argument("--sd", type=float, default=None)
    parser.add_argument("--bar", type=float, default=None)
    parser.add_argument("--corpus-best", type=float, default=None)
    parser.add_argument("--skew", type=float, default=0.0)
    parser.add_argument("--kurtosis", type=float, default=3.0)
    return parser.parse_args(argv)


def effective_n(n_obs: int, n_series: int | None, n_eff: float | None) -> int:
    """Deflate pooled observations by the correlation deflator.

    Pooling ``k`` correlated series carries the noise reduction of ``n_eff``
    independent ones, so the naive ``n`` overstates the information. Omitting
    the deflator on a pooled multi-symbol panel is a declared error rather than
    a default, which is why both flags must be supplied together.
    """
    if n_series is None and n_eff is None:
        return n_obs
    if n_series is None or n_eff is None:
        raise ValueError("--n-series and --n-eff must be supplied together")
    if n_series < 1 or n_eff <= 0.0:
        raise ValueError("--n-series must be >= 1 and --n-eff must be > 0")
    if n_eff > n_series:
        raise ValueError(
            f"n_eff ({n_eff}) cannot exceed n_series ({n_series}): the deflator "
            "counts effective independent series among n_series, so n_eff <= "
            "n_series always. Check the argument order."
        )
    return max(2, int(n_obs * n_eff / n_series))


def check_footing(args: argparse.Namespace) -> None:
    """Refuse a footing declaration the inputs contradict. Raises ``ValueError``."""
    ppy = args.periods_per_year
    if ppy is not None and ppy <= 0.0:
        raise ValueError("--periods-per-year must be > 0")
    if args.sr_footing == "annual":
        if ppy is None:
            raise ValueError(
                "--sr-footing annual needs --periods-per-year: PSR runs per "
                "observation, so the annualized inputs must be converted"
            )
        if args.sd is not None:
            raise ValueError(
                "--sd does not apply under --sr-footing annual: --corpus-best "
                "and --bar are annualized Sharpes there, not effect units"
            )
        return
    if args.units != "per_book_day":
        return
    corrective = f"pass --sr-footing annual --periods-per-year {BOOK_DAYS_PER_YEAR:g}"
    implied = math.sqrt(max(args.sr_variance, 0.0) * BOOK_DAYS_PER_YEAR)
    if implied > MAX_PLAUSIBLE_ANNUAL_SHARPE:
        raise ValueError(
            f"--sr-variance {args.sr_variance} per book day implies a trial-family "
            f"Sharpe dispersion of {implied:.2f} annualized, above the "
            f"{MAX_PLAUSIBLE_ANNUAL_SHARPE:g} no filed sleeve approaches. If it is "
            f"the variance of annualized Sharpes, {corrective}"
        )
    if args.corpus_best is not None and args.sd is not None and args.sd > 0.0:
        implied = abs(args.corpus_best / args.sd) * math.sqrt(BOOK_DAYS_PER_YEAR)
        if implied > MAX_PLAUSIBLE_ANNUAL_SHARPE:
            raise ValueError(
                f"--corpus-best {args.corpus_best} per book day implies an "
                f"annualized Sharpe of {implied:.2f}, above the "
                f"{MAX_PLAUSIBLE_ANNUAL_SHARPE:g} no filed sleeve approaches. If "
                f"it is an annualized Sharpe, {corrective}"
            )


def price(args: argparse.Namespace) -> list[str]:
    """Build the report. Returns lines; printing is the caller's job."""
    check_footing(args)
    n_used = effective_n(args.n_obs, args.n_series, args.n_eff)
    annual = args.sr_footing == "annual"
    ppy = args.periods_per_year
    # PSR runs per observation, so annualized inputs come down by sqrt(P) for a
    # Sharpe and by P for a variance before pricing, and the bar goes back up.
    scale = math.sqrt(ppy) if annual else 1.0
    sr_variance = args.sr_variance / scale**2
    sr = required_sharpe(
        n_used,
        n_trials=args.n_trials,
        sr_variance=sr_variance,
        skew=args.skew,
        kurtosis=args.kurtosis,
    )
    reachable = not math.isinf(sr)

    if annual:
        footing = (
            f"annual, converted at {ppy:g} periods/year (PSR runs per observation)"
        )
    else:
        footing = "per_obs"
    out = [
        "distil_power - G3 power gate",
        f"  units             {args.units}",
        f"  Sharpe footing    {footing}",
        f"  n_obs (declared)  {args.n_obs:,}",
    ]
    if n_used != args.n_obs:
        out.append(
            f"  effective n       {n_used:,}"
            f"  (deflated by n_eff {args.n_eff} / {args.n_series} series)"
        )
    else:
        out.append("  effective n       (no deflator applied)")
    family = (
        f"  trial family      {args.n_trials} trials, sr_variance {args.sr_variance}"
    )
    if annual:
        family += f" annual = {sr_variance:.8f} per obs"
    out += [
        family,
        f"  gate target       DSR >= {GATE_DSR}",
        "",
    ]

    if not reachable:
        out.append("  VERDICT           UNREACHABLE at any finite Sharpe")
        out.append(
            "                    Not underpowered - unreachable. More data of this"
        )
        out.append(
            "                    shape cannot fix it; the trial family must shrink."
        )
    elif annual:
        out.append(f"  required Sharpe   {sr * scale:.6f} annual  ({sr:.6f} per obs)")
        if args.corpus_best is not None:
            out.append(f"  corpus best       {args.corpus_best:+.4f} annual")
            if sr * scale > args.corpus_best:
                out.append(
                    "  VERDICT           REACHABLE, but the bar EXCEEDS the corpus best"
                )
            else:
                out.append("  VERDICT           REACHABLE")
        else:
            out.append("  VERDICT           REACHABLE")
    else:
        out.append(f"  required Sharpe   {sr:.6f}")
        if ppy is not None:
            out.append(
                f"                    = {sr * math.sqrt(ppy):.6f} annualized"
                f" at {ppy:g} periods/year"
            )
        if args.sd is not None:
            unit_noun = args.units.replace("per_", "").replace("_", " ")
            out.append(
                f"  required effect   {sr * args.sd:+.4f} per {unit_noun}"
                f"  (sd {args.sd})"
            )
        if args.corpus_best is not None:
            out.append(f"  corpus best       {args.corpus_best:+.4f}")
            if args.sd is None:
                # `--corpus-best` is denominated in EFFECT units and `sr` in
                # Sharpe, so without `--sd` there is no conversion between them
                # and the comparison the flag was passed FOR cannot run.
                #
                # Saying so in the verdict is the whole point. G3 MANDATES this
                # tool, so a bare REACHABLE here is indistinguishable from one
                # that actually cleared the corpus best -- did-not-compare reads
                # as passed, which is the failure mode the gate exists to stop.
                out.append(
                    "  VERDICT           REACHABLE, but the corpus best was"
                    " NOT COMPARED"
                )
                out.append(
                    "                    --sd is required to convert the required"
                )
                out.append(
                    "                    Sharpe into effect units. Without it this"
                )
                out.append(
                    "                    run cannot say whether the bar exceeds the"
                )
                out.append("                    corpus best. Re-run with --sd.")
            elif sr * args.sd > args.corpus_best:
                out.append(
                    "  VERDICT           REACHABLE, but the bar EXCEEDS the corpus best"
                )
            else:
                out.append("  VERDICT           REACHABLE")
        else:
            out.append("  VERDICT           REACHABLE")

    if args.bar is not None and (annual or args.sd is not None):
        # Under `annual` the bar is an annualized Sharpe, so the CI is the
        # per-observation Sharpe's (sd 1) scaled back up by sqrt(P).
        sd = 1.0 if annual else args.sd
        half = Z_95 * sd / math.sqrt(n_used) * scale
        licensed = powered_null(-half, half, bar=args.bar)
        suffix = " annual" if annual else ""
        out += [
            "",
            f"  null bar          +/-{args.bar}{suffix}",
            f"  CI half-width     {half:.4f}{suffix}"
            "  (best case, point estimate exactly 0)",
            f"  powered null      {'LICENSABLE' if licensed else 'NOT LICENSABLE'}"
            f"  (analytics.audit_guard.powered_null)",
        ]
        if not licensed:
            out.append(
                "                    A null here would be INSUFFICIENT, never 'no effect'."
            )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        lines = price(args)
    except ValueError as exc:
        print(f"distil_power: {exc}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    from utils.stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
