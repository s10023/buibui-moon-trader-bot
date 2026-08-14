# ST17 — pricing the era study: the scan is unreachable, one comparison is not

**Date:** 2026-08-14.
**Prices:** SoT ST17's remaining half — "re-cut filed results by era and measure how
much moves" — enabled by the era instrumentation shipped 2026-08-14d (`analytics/eras.py`).
**Code:** `tools/era_power_price.py` (tracked on purpose — see below), `tests/test_era_power_price.py`.
**Scope:** ledger, keyed on `fired_at_ms`. The backtest scope is NOT priced here.

## Verdict

**The per-era scan is structurally unreachable and must not be built. One
pre-registered era comparison is reachable and is the only shape worth running.**

**And the study's most likely conclusion — "we re-cut by era and nothing moved" —
is not licensable at 43 of 44 eras.** Filing those as nulls would have been the
seventh powered-null site.

No design was written before this ran, which is the point: the pricing changed the
deliverable from a scan to a single comparison, and attached a precondition to the
null half that the study would otherwise have failed silently.

## The sample, measured rather than inferred

| quantity | value |
| --- | --- |
| resolved alerts | 4,902 |
| mean / sd | −0.1310R / 1.1606R per alert |
| skew / kurtosis | +1.407 / 4.781 (both fed to the PSR) |
| symbols | 3 (BTC/ETH/SOL), 109–112 days each |
| cross-symbol `n_eff` | 1.64 independent series, t-deflator **1.352×** |
| **effective n** | **2,681, not 4,902** |
| occupied eras | 44 of 60 ledger boundaries |
| largest era | 982 alerts (20%); median era 40; 12 eras at n≥100 |

The deflator corrects **cross-symbol** correlation only. Alerts also cluster in
time within a symbol — several timeframes and strategies firing on one move — and
that is not deflated, so **every effective n below is an upper bound and every bar
an under-estimate**. The direction of that error is toward the verdict, not away
from it.

## Leg A — the gate bar (DSR ≥ 0.95, per-alert Sharpe and R/alert)

| era n (raw) | n eff | k=1 | k=12 | k=27 | k=44 |
| --- | --- | --- | --- | --- | --- |
| 982 | 537 | 0.068 / **+0.079R** | 0.691 / +0.802R | 0.832 / +0.966R | 0.909 / **+1.055R** |
| 500 | 273 | 0.093 / +0.108R | 0.711 / +0.825R | 0.852 / +0.989R | 0.930 / +1.079R |
| 250 | 136 | 0.129 / +0.150R | 0.740 / +0.858R | 0.882 / +1.024R | 0.961 / +1.115R |
| 100 | 54 | 0.197 / +0.229R | 0.798 / +0.927R | 0.945 / +1.096R | 1.026 / +1.191R |
| 50 | 27 | 0.268 / +0.311R | 0.868 / +1.007R | 1.022 / +1.186R | 1.109 / +1.287R |

**Scanning all 44 occupied eras demands +1.055R per alert on the largest era.** The
book runs −0.131R and the corpus best single cell is ≈ +1.196R, so the scan asks a
single era to reproduce the best result this system has ever produced. Restricting
to the 12 eras at n≥100 does not rescue it: +0.802R.

**One pre-registered comparison needs +0.079R on the largest era** — assumption-free,
since k=1 carries no deflation term at all. Observed era-Sharpe dispersion is
var 0.149 (sd ≈ 0.386, ≈ 0.45R), so era-to-era movement of that size demonstrably
exists in the sample. That is the reachable design.

This reproduces the standing rule exactly: a 21× range of n moves the bar ~10%,
while 1 → 44 trials moves it **13×**. Trial count dominates, and it is not close.

## Leg B — the null bar (CI containment, not `|Δ| < MDE`)

| era n (raw) | n eff | 95% CI half-width | vs 0.05R bar | vs 0.10R bar |
| --- | --- | --- | --- | --- |
| 982 | 537 | 0.0982R | 2.0× too wide | **CONTAINED** |
| 500 | 273 | 0.1377R | 2.8× too wide | 1.4× too wide |
| 250 | 136 | 0.1951R | 3.9× too wide | 2.0× too wide |
| 100 | 54 | 0.3095R | 6.2× too wide | 3.1× too wide |
| 50 | 27 | 0.4378R | 8.8× too wide | 4.4× too wide |

**Only the single largest era can license a null, and only against a 0.10R bar.**
Against the filed 0.05R precedent from the H14/H15 panels, nothing qualifies —
including the largest era, at 2.0× too wide.

So a study that cut 44 eras and reported "no era differs" would be publishing 43
INSUFFICIENT cells as powered nulls. That is the seventh site, pre-empted. The
verdict here is computed by `analytics.audit_guard.powered_null`, not restated
inline, so the criterion cannot drift from the one the other six were corrected to.

## Two process notes

**The driver is tracked, not scratch.** ST28's sixth site sat in gitignored scratch
code where no gate, grep or review surface could reach it, and its spec and driver
disagreed on the detection threshold. `tools/era_power_price.py` is linted,
type-checked and tested (13 cases), and inverts the *production* `deflated_sharpe_ratio`
numerically rather than re-deriving a closed form — a hand-derived bar is how a spec
and its implementation come to disagree while both stay internally consistent.

**The context-guard hook caught a real defect — but it was the CLAIM trigger, not a
path card.** The first draft of this tool hand-rolled the containment comparison
instead of calling `powered_null`; the hook fired on the write, named the rule, and
the call was replaced. That is a genuine catch and the second-ever fire of that
trigger (after `analytics/eras.py`, 08-14d).

**It does NOT discharge the open question from 08-14e.** The risk the shed took on
was that a *path card* — the `analytics/` / `trade/` / `portfolio/` globs carrying
the seven footgun paragraphs `CLAUDE.md` gave up — would fail to appear at edit
time. This session wrote to `tools/`, so no path card was in scope and none fired.
**The path-card half remains untested; the next session editing a guarded file is
still the first genuine test.**

## What this does NOT price

The **backtest** scope (the decay review's rated pool, 90 boundaries) is not priced
here. It carries a ~5.29× duplication factor and needs dedup on
`(symbol, timeframe, strategy, direction, entry_time)` before any n is meaningful,
so it is a separate pricing, not an extension of this one.
