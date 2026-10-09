# Round 1 long list, power-priced at N = 3

Ticket: <https://github.com/s10023/buibui-moon-trader-bot/issues/920> · Date 2026-10-08 · map #864

Follow-on: #850 (E850, round 2 slot 1), #983 (slots 2-3).

## Answer

The long list holds 24 candidates. All 21 that could be priced clear the corpus best on power alone, so that comparison decides nothing. What separates them is each candidate's own measured or claimed effect against its N = 3 bar. Only two carry a filed effect above the conservative bar:

- H8's VWAP location gate (#836): +0.485R against a bar of +0.283R. It counts only after the A31 re-run that #908 makes conditional on choosing it.
- H6's reference-level near cell (#845): a lift of +0.725R, CI lower bound +0.233R, against +0.325R. This holds only once its live cell reaches n = 100, a count not yet re-read.

Two more clear their bar only at the low variance:

- H1 violent-up continuation: +4.6pp against +3.6pp / +2.5pp. Its correlation to XS is expected to be high and must be measured first.
- H18 OI flush on the #936 backfill (#850): no prior effect estimate. Its bar is 1.11 annualised at low variance, under XS's 1.375, but 1.90 at high variance.

Five more are unreachable or unpriceable:

- **The quarter-hour imbalance lead behind #937:** its best published effect, 16.9 bps gross at 12h, is close to the 14 bps cost of one round trip. That leaves ~3 bps net (taker) or ~9 bps (maker), against a bar of 14.6–24.6 bps.
- **Three more are unreachable:** the social/attention composite (about three cycle observations), BTC range duration (about 25 ranges) and the heatmap track record (23 days) are all too small for any effect to clear.
- **Two cannot be priced:** liquidation recording has no history yet, and the prediction-market lead has no defined construction.

H13 keeps slot 1 by ruling. It is reachable on power (+0.086R per book-day at high variance, under its K1 kill line of +0.15R), but its own control reads −0.146R per book-day, so it needs a swing of about +0.23R.

## The list

Variance columns: **hi** = the measured per-trade family variance 0.0165 for per-trade rows, or the 0.005 floor for book-day and event rows; **lo** = 0.005 per-trade, 0.0005 elsewhere (see Method). Required effects are net of modelled cost. Per-trade rows are in R; event rows are in % return per event; book-day rows are in R per book-day.

**Verdicts.** **PRIOR** = the candidate's own filed or measured effect clears the hi bar. **PRIOR-lo** = it clears only the lo bar. **POWER** = the bar is under the corpus best, but no own effect clears it (or none exists). **UNREACH** = more data of the held shape cannot get there, or the effect is far below the bar. **n/a** = not priceable yet.

| ID | Source | Construction (one line) | Book · hold | Required, hi / lo | Own effect | Verdict | Corr to XS (expected) | Data |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| H13 | #848, spec #935 | 15m ORB at the DST-aware NYSE cash open, majors, tp_r 1.5 | bot · to cash close | +0.086 / +0.050 R/book-day (1.59 / 0.94 ann.) | control (00:00 orb, 15m majors) −0.146 R/book-day, n 250 | POWER; slot 1 by ruling | low (intraday, session-timed) | held 15m |
| C836 | #836, register A31/I13 | gate the detector book on weekly-VWAP location: shorts below, never longs below | bot + manual · detector holds | +0.283 / +0.195 R | +0.485 R (4h below/short), lift +0.910 | **PRIOR**, after the A31 re-run | low-moderate (location is continuation-flavoured) | held; A31 re-run with cluster key first |
| C845 | #845, register A25/I05 | reference-level proximity (PDL, W/M high-low, DO/WO/MO) on the live long-near cell | bot + manual · detector holds | +0.325 / +0.266 R at n 100 | lift +0.725 R, CI [+0.233, +1.267] | **PRIOR** at its own n ≥ 100 trigger | low | held; re-run `reference_level_proximity_audit.py` to read live n |
| E841 | #841 (H1, adjacent result) | long the universe after a violent up day (ret ≥ 5%, z ≥ 2.5), hold 5d | bot · 5d | +3.61 / +2.50 % per date (1.13 / 0.78 ann.) | +4.60pp excess, n 506 dates (reproduced; filed +3.63pp, 412) | **PRIOR-lo** | high (time-series momentum) | held 1d |
| E850 | #850 (H18), #936 | resistance break on rising OI predicts a flush | bot · intraday-days | +0.099 / +0.058 R/book-day (1.90 / 1.11 ann.) at 1,772 days | none measured | POWER; under XS 1.375 only at lo | unknown, plausibly low (positioning) | #936 backfill (majors from 2021-12) |
| E847 | #847 (H12) | weekly cross-sectional continuation beyond XS-solo | bot · 1w | 0.146 / 0.105 Sharpe per week (1.05 / 0.76 ann.) | h96 +0.056 AWR/wk, DSR 0.972 at its own family | POWER; correlation check gates it | high (honest prior: same alpha) | held |
| C825 | #825, register A57 | relax engulfing's strict open inequality; one pooled test | bot · detector holds | +0.233 / +0.142 R | pooled engulfing +0.056 R (measured; filed +0.114), live −0.13 to −0.17 | POWER (filed as reachable at 1 trial only) | low | held |
| C795 | #795, register A29 | ATR-scaled stop for the six candle detectors at 1h/4h | bot · detector holds | +0.234 / +0.145 R | candle-6 1h/4h pool +0.044 R; 15m is CONFIRMED-BAD | POWER; a positive needs 15m tie resolution | low | held, 15m bars to resolve ties |
| C820 | #820, M34 | orb 1h/4h structural target instead of derived tp_r | bot · detector holds | +0.292 / +0.204 R | orb 1h −0.031 R, 4h −0.002 R; target change unmeasured | POWER; the measurement comes first | low | held (`compute_excursions`) |
| C823 | #823, M35 | BTC/ETH SMT agreement gates 15m shorts | bot · detector holds | +0.241 / +0.161 R | 15m majors pool −0.098 R; conditioning axes 6-for-6 | POWER | low | held |
| C843 | #843 (H3), register A27 | trade fib golden zone at first touch only | bot · detector holds | +0.273 / +0.196 R | fib_golden_zone −0.049 R; structural first-touch withdrawn as look-ahead | POWER, prior negative | low | held; A27 re-run first |
| C849 | #849 (H16) | diagonal trendline break, incremental over a co-firing bos | bot · detector holds | +0.224 / +0.149 R | bos −0.057 R; prior against | POWER, only as one pinned construction | low | held |
| C846 | #846 (H7, ST8) | staleness exit (BE or flat after typical winning duration) on the live book | bot · exit policy | +0.148 / +0.090 R | time-stop sweep negative at every setting | POWER; blocked on #924 | low | held |
| C931 | #931, #844 (H4/H5) | range funnel: range → deviation and reclaim → target → sizing | manual + bot · range rotation | +0.370 / +0.289 R at an assumed n 200 | location prior against: long-below-VWAP −0.439 R backtest / −0.654 live; the reclaim trigger itself is untested | POWER; n unknown until the stage-1 range census | low | held 1h/4h |
| E814 | #814, inbox 2026-07-02 | BTC 2d move into/after scheduled FOMC meetings | bot · 2d | +1.24 / +1.06 % per meeting at n 56 | claimed ~14% reversals on 2 instances | POWER; window must be pinned first | ~0 | FOMC date list (public) |
| E_yvwap | inbox 2026-09-09 | reaction at the developing yearly VWAP ±1σ band | bot · 5d | +1.25 / +0.76 % per date, n 1,309 | none; direction not stated | POWER; re-slice of C836's location axis | moderate | held 1d |
| E_lunar | inbox 2026-07-31 | forward 14d return by new/full moon | bot · 14d | +2.93 / +2.42 % per phase at n 87 | single anecdote, no mechanism | POWER; do not build | ~0 | computed calendar |
| E937 | #937, #909 lead | quarter-hour opening taker imbalance predicts 4–12h | bot · 12h | +0.246 / +0.146 % per 12h window (n_eff 1.14 of 3) | 16.9 bps gross at 12h (IQR move) minus 14 bps cost ≈ +3 bps net | **UNREACH** net of cost at the paper's effect | low (intraday flow) | #937 build |
| E_rdur | inbox 2026-08-14 | elapsed time in a BTC daily range predicts resolution | manual · weeks | +5.00 / +4.47 % per range at n ≈ 25 | none; ranges labelled by hand | **UNREACH** (0.41 sd per range) | low | a range labeller (none exists) |
| E_social | inbox 2026-07-23 ×2, 2026-07-20 | retail attention / social composite as a contrarian state | manual · months | +27.3 / +25.7 % per cycle at n_eff ≈ 3 | levels only, no forward test | **UNREACH**: one observation per cycle | moderate | collectors to build |
| E835 | #835 | does price reach and reverse at mapped heatmap clusters | manual · 1–7d | +1.85 / +1.66 % per day at n 23 | never measured | **UNREACH** as an edge; descriptive only | low | on disk |
| E940 | #940, #913 | liquidation-originated moves reverse | bot · 2h | not priceable | 61% vs 43% reversal, p ≈ 0.10, gross | n/a: record-forward, n = 0 | low | recorder host |
| E939 | #939 | prediction-market odds | — | not priceable | none | n/a: construction not defined, parked | — | route unprobed |
| A33 | register A33 (H14) | Coinbase premium as a state tag | bot | not re-priced | all 10 cells INSUFFICIENT at held n | n/a until the held n changes | low | held |

## Re-runs that take no slot (per #908)

These are faithful re-runs: each replaces its original trial and costs nothing from the round budget.

- #923 A08: the 10 live volume_suppress flips.
- #924 A15: the composite exit at 1h/4h. It blocks #921 and every exit-shaped row above (C846, and the manual book's direction).
- #925 A09: the direction axis, re-split without pre-fix bos.
- Two are owed only if their candidate is chosen. The A31 re-run comes before C836, and A27 before C843.

## For #921

- **Already decided:** H13 holds slot 1.
- **Next in line on this pricing:** C836, then C845. Both are gates on the existing detector book, so they share its book-day axis and give PBO its second and third columns without a new search.
- **XS overlap, if E841 or E847 is considered:** measure correlation to XS first. If either tracks XS, it is not a second edge.
- **E850, if it is considered:** it carries new information. Its bar sits under XS only at the low variance, and nothing has measured its effect.
- **Not for round 1:** E937. It should not take a round-1 slot on the paper's effect, and the #937 build is worth holding until a construction with a larger per-trade effect is specified.
- **Calendar mismatch:** H13's book-days are NYSE trading days, while the detector book's are calendar days. #921 has to pick one axis.

## Method

- **No verdict here is a null.** POWER and UNREACH compare a required bar with a claimed or measured effect. None of them says "no effect": nothing was tested, and only `analytics.audit_guard.powered_null` licenses a null.
- **Tool:** every required effect comes from `tools/distil_power.py` at `--n-trials 3`, always with `--sd`. Driver `price.py`, raw output `price.out`.
- **Inputs:** read-only from `analytics.db` on 2026-10-08 (`measure.py`, `measure_orb.py`), or from the filed audit named in the row. Backtest trades are deduplicated on (symbol, timeframe, strategy, direction, entry_time): 166,662 distinct.
- **Variance:**
  - **Per-trade hi** is the measured per-trade Sharpe variance over 101 deduplicated cells with n ≥ 100: 0.0165. ST87 measured 0.0224 on its own family.
  - **Book-day and event rows** have no measured round-1 family. H13's K1 convention, max(measured, 0.005), sets hi = 0.005 and lo = 0.0005. The six sleeves' annual Sharpes give 0.0054 per day with xsrev included and 0.0011 without it, which brackets that range.
  - **Effect on the bar:** at N = 3 the variance term dominates every row with n in the thousands, so the variance choice moves a bar more than doubling n would.
- **Corpus best:**
  - **Per trade:** +0.569R. This is the best deduplicated cell with n ≥ 100 (smt_divergence/1h/short), lower than the +1.196R filed on the multi-regime panel, so the comparison is the stricter one.
  - **Book-day and event rows:** compared to XS's +1.375 annualised. Event rows annualise at their own event rate, which flatters a sparse event book against a continuously invested one.
- **Cost:** round trip `2 × (fee 0.05% + slippage 0.02%)` = 0.14% of entry, from `config/strategy_params.toml`. Per-trade R rows inherit the backtest's net R. Event rows' bars are net, so a gross claim must clear bar + 0.14%. That is immaterial except for E937, where it decides the verdict.
- **Deflators:**
  - **Majors panels:** use n_eff 1.82 of 3, computed for #825. ST87 computed 1.549 for orb.
  - **E937:** uses the 12h-return correlation measured here (ρ 0.81, n_eff 1.14 of 3).
  - **Day-aggregated event rows:** aggregated per date, so no series deflator.
  - **C823:** its n is the 20% coverage floor from its spec, divided by the trade-weighted DEFF 4.99.

## Caveats

- **Estimated n.** C836's n (1,900) is half the deduplicated 4h short pool, not the audit's exact cell count. C931's 200 and C849's 1,930 are assumptions until each construction's census runs. C823's is its spec's floor.
- **C845's prior is a lift, not a cell mean.** The construction must be defined as the gated book before #921 can compare it with the others.
- **E841 is a reproduction.** z is the return over the prior 20-day sd, which gives 506 dates against the filed 412. Its sd (27%) is driven by alt-coin tails, and its 5-day windows overlap across adjacent event dates, so effective n is lower than 506.
- **E850's n is calendar days.** The real construction is event-based, so its true n is the number of qualifying breaks. That is smaller, which raises the bar.
- **Expected correlation to XS is a judgement for every row.** None was measured here, because the ticket asks for it as expected. E841 and E847 are the two where measuring it is the first step.
- **Excluded:** the mechanics backlog (Stream B), which has no per-item status (#917); and #818 / ST134, which are validation work, not candidates.

## Sources

- Issues #820 #823 #825 #835 #836 #841 #843 #844 #845 #846 #847 #849 #850 #931 #937 #939 #940, and the map #864 with its closed tickets #907 #908 #909 #913 #915 #916 #917 #919.
- Tested register: `docs/research/2026-10-07-tested-register.md`, rows A08 A09 A15 A25 A27 A29 A31 A33 A57 I01–I16 M32–M35.
- Literature: `docs/research/2026-10-08-literature-nonprice.md`, Kim and Hansen, arXiv:2607.09426.
- Audits: `docs/audits/2026-07-24-h8-m1-indicator-conditioning.md`, `docs/audits/2026-09-06-st110-engulfing-recall-power.md`.
- Specs: H13 `docs/superpowers/specs/2026-10-08-h13-us-open-orb-preregistration.md`; SMT `docs/superpowers/specs/2026-08-29-st105-smt-gate-prereg.md`.
- Thesis inbox rows 2026-07-02, 2026-07-20, 2026-07-23 (×2), 2026-07-31, 2026-08-14, 2026-09-09: the seven open, unpriced rows from #917.
