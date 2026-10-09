# E841 — hedged violent-up continuation out of universe: killed at K1

Runs round 1's third trial (#948) exactly as pre-registered in
`docs/superpowers/specs/2026-10-08-e841-violent-up-holdout-preregistration.md` (decided in #945,
merged in PR #947). The driver is `tools/e841_holdout.py`, which builds §3–§5 as written. It
reads a 2026-10-09 00:50 UTC snapshot of `analytics.db`, taken after the 402 holdout symbols and
the 25 universe symbols were re-synced on 1d, so every bar dated 2026-10-08 is the CLOSED bar.

## Verdict

E841 is killed at K1 and does not run. On the holdout's book-day series (n 2,142, sd 0.906R),
the DSR leg at N = 3 and the 0.005 variance floor needs an annualised Sharpe of 1.833, and the
same §3 construction run on the 25 universe perps, the yardstick §5 fixes, realises +1.599.
The required Sharpe exceeds the yardstick, so §5 says do not run E841, and the slot stays empty
(#921). This is a failure to clear the power check, never a null and never "no edge": no mean
of the holdout series was computed, so nothing here says whether the effect exists out of
universe. The kill does not depend on round 1's other columns. §5 prices K1 at the larger of
the measured cross-column Sharpe variance and 0.005, and a larger variance only raises the bar,
so no later column can rescue it. At this variance the bar falls to the yardstick only at about
4,953 book-days, 2.3 times what the holdout holds, so waiting for a forward record is not a
practical route either. K2 reproduced #920 on every count; its one difference, the baseline, is
explained below and traced to the forming 2026-10-08 bar #920 read.

## What ran, in the spec's order

**K2 fidelity** (raw trigger on the 25 universe perps, panel cut at the 2026-10-08 bar):

| | symbol-days | dates | per-date mean | baseline |
| --- | --- | --- | --- | --- |
| #920 | 1,079 | 506 | +5.745% | +1.146% |
| this run | 1,079 | 506 | +5.745% | +1.144% |

The baseline difference is the 2026-10-08 bar closing. Of 43,996 universe 1d bars up to
2026-10-08, the only rows that differ between the 2026-10-08 12:56 UTC snapshot and this one
are the 24 dated 2026-10-08. The same code reads +1.1462% on the forming bar, which
reproduces #920 exactly, and +1.1445% on the closed one. Counts and the event mean are byte-identical,
because no universe trigger falls on 2026-10-03, the only trigger date whose five-day return
reads the 2026-10-08 close. The truncation test (`tests/test_e841_holdout.py::TestTruncation`)
passes, and an injected peeking variant fails it.

**The holdout build** (no mean read): all 402 symbols returned bars, so none was dropped.
9,225 triggers; 359 inside a symbol's first 60 days, 3,234 under the $5M liquidity floor, 874
skipped while the symbol was already held, 5 whose exit bar falls after 2026-10-08. That
leaves 4,753 positions on 362 symbols, none spanning a calendar gap, and 2,142 book-days.

**K1 power** (printed before any mean of the holdout series):

| Input | Value |
| --- | --- |
| book-days n / sd | 2,142 / 0.9060R |
| `--sr-variance` | 0.005 (floor; one column exists, so no cross-column variance is measurable) |
| `--n-eff` | not passed (§5); the holdout's n_eff is 2.989 over 402 symbols (deflator 11.60), disclosure only |
| required Sharpe (`tools/distil_power.py`) | 0.0959 per book-day = 1.833 annualised; effect +0.0869R per book-day |
| yardstick | +1.599 annualised: 859 positions, 1,363 book-days on the 25 universe perps |

The gate was not reached, so there is no DSR, boot_lo, PBO or `powered_null` reading, and no
book-day series was written for round 1's PBO step.

## Readings fixed before any holdout bar was read

The spec left three things implicit. Each was fixed in the driver and agreed with the operator
before the holdout was read:

- **Re-trigger:** a position holds bars D+1 to D+5, so a trigger on any of them, D+5
  included, is skipped.
- **Daily P&L:** marked to market on entry notional (§4 calls each row "one day's
  mark-to-market P&L"). Both legs hold a fixed quantity from the D+1 open and the hedge basket
  is never rebalanced, so a position's rows sum to N × (long five-day return − basket
  five-day return), before cost.
- **DSR variance:** §4 names no trial variance, so it is K1's, max(measured, 0.005), the only
  reading under which K1 prices the same leg the gate applies.

None of the three is reached by the kill, except that the first two shape the book-day count
and sd K1 used.

## What this leaves for round 1

C836's slot emptied when the causal H8 tagger landed (#961), and E841's is now empty too. So
H13 is round 1's only remaining candidate, and its PBO leg has no other candidate column
beside it. #921 named the shipped 00:00 UTC orb as H13's fallback second column. Whether that
applies now is the operator's call. N stays fixed at 3, because the unfilled slots expire with
the round and are never back-filled.

## Caveats

- **The yardstick is in-sample.** It runs the construction on the data that nominated it, which
  §5 accepts because it only decides whether the holdout can run. Read +1.599 as an upper
  reference, never as a measured edge.
- **Survivorship and shared dates** (spec §8) would bias a run in continuation's favour. They
  do not bear on a kill decided by n, sd and the yardstick.
- **Costs are modelled**, read at run time from `config/strategy_params.toml` (fee 5 bps,
  slippage 2 bps per leg), and enter K1 only through the yardstick's own net series.
