# #985 — seven verdicts re-run through the one `backtest_trades` loader

Re-runs the seven verdicts #949 found resting on polluted `backtest_trades` rows, now that
every reader goes through `analytics.store.load_backtest_trades` and #987's writer replaces a
run's whole trade set. Read-only against a scratch copy of `analytics.db` taken 2026-10-09
10:14 UTC, after the 2026-10-09 `/db-update` (backtest + recalibrate) ran on the fixed
writer. The ST28 legs were regenerated into a second copy; the live DB was never written.

## Verdict

No research NO reverses, but three config decisions lost the verdict they were written from,
and H14 is the only re-run in which nothing moved.
ST28's NO stands with a different fourth cell, and ST63's UNREACHABLE verdict stands. H8 falls
from 2 BUILD and 19 AVOID to 0 BUILD and 10 AVOID, and still finds no gate-grade short-side
price-location cell. The `bos/1d/long` "verified post-fix" claim is
withdrawn, because no post-fix `bos` 1d row exists. The weekend `liquidity_sweep` 1h short
volume flip and both `bos` short ADR exemptions lost the DISABLE verdicts they were written
from: each now reads INSUFFICIENT, and two of the three point estimates are negative. "`bos`
shorts beat longs" holds at 1h and 4h and reverses at 15m. H15's ledger near-miss tightened to
six of seven gate legs, with DSR 0.9496 against 0.95.

Three of these move live config or stored ratings, and none is changed here, because writing
the TOMLs is the deployment. The weekend volume flip is already #971. The two ADR exemptions
are #992. The stale weekend `bos/4h` rating is #993.

## The pool, and why the floor alone was not enough

The 2026-10-09 `/db-update` saved its runs under new `run_id`s (the run_id now carries every
engine axis, ST86), so it did not overwrite the 2026-08-18 runs, and those still hold their
pre-fix rows. Measured on the snapshot by replaying HEAD's `detect_market_structure` /
`detect_liquidity_sweep` against every stored row:

| selection | leaked `bos` rows | causal `bos` keys | leaked `liquidity_sweep` rows |
| --- | --- | --- | --- |
| every row | 82,901 | 945 | 5,018 |
| `run_at_ms` ≥ floor (2026-08-18T13:21Z) | 4,233 | 945 | 0 |
| floor + clean run (closed rows = `closed_trades`) | 0 | 945 | 0 |

All 4,233 sit in the 27 `bos` runs of 2026-08-18 whose stored rows disagree with their own
aggregate. The loader therefore admits a floored detector only from clean runs at or after the
floor. Applying the clean-run check to every detector would drop 30% of the other detectors'
distinct keys (146,635 → 103,440) on no detector change, so it is scoped to floored ones.
Deduplicated, the pooled table is 148,303 trades where H14 and H15 previously read 849,445 raw
rows.

## The seven

1. **H8** (`2026-10-08-h8-a31-causal-tagger-rerun.md`) — MOVED; the headline that no
   short-side location cell is gate-grade STANDS.

   | tier | filed BUILD / AVOID | clean pool BUILD / AVOID |
   | --- | --- | --- |
   | 4h | 2 / 8 | 0 / 1 (`vp_value_area/above/long`) |
   | 1h | 0 / 9 | 0 / 9 |
   | 1d | 0 / 2 | 0 / 0 |

   Both 4h BUILDs are gone, as #949 predicted. C836's 4h `vwap_weekly/below/short` reads
   +0.130R, INSUFFICIENT (filed +0.160R), and the 4h `vwap_weekly/above/long` example the
   filed audit quotes is now INSUFFICIENT. 1h keeps nine AVOIDs, eight long and "extended
   up"; the ninth is `bb_squeeze/squeeze/short`, a volatility state rather than a price
   location. The surviving cells differ from #949's causal-only pool (which kept 2 at 4h
   and 7 at 1h) because the loader also changes which run's row survives dedup, so the
   AVOID set moves with the selection rule. That is one more reason to read it as ~1
   collinear in-sample finding rather than nine.
2. **ST63 `bos/1d/long`** (`2026-08-24-st63-occurrence-dump-power-pricing.md`, and the
   `AGENTS.md` sentence repeating it) — MOVED: the "VERIFIED post-fix" section is withdrawn.
   The last `bos` 1d run was saved 2026-05-22, all 475 of its rows are pre-fix, and the loader
   returns 0 `bos` 1d rows. The dispersion-floor mechanism needs no such cell to stand. The
   UNREACHABLE verdict STANDS, since it rests on trial count rather than on any `bos` level.
3. **Volume-suppress flip** (`2026-05-21-volume-suppress-mon-fri-weekend.md`) — MOVED.
   Weekend `liquidity_sweep` 1h short, scored with `gate_audit`'s own table on the 2026-10-09
   runs: the low-volume slice the flip un-suppressed reads **−0.265R, n=319, CI [−0.626,
   +0.151], INSUFFICIENT**, against the filed DISABLE at +0.177R (n=51). Kept slice −0.074R
   (n=213). These runs carry no live-parity stack, while the filed audit ran one, so this is a
   corroborating read rather than a faithful repeat. It agrees with #971's faithful re-run.
   The weekdays flip sits on a cell no config declares (`liquidity_sweep = ["1d"]`), so it is
   inert.
4. **Direction axis** (`2026-06-03-direction-axis-hard-flip.md`, backtest leg) — MOVED at
   15m. Causal `bos` rows, deduped: 15m long −0.079R (n=350) vs short −0.393R (n=344); 1h
   long −0.594R (95) vs short −0.020R (98); 4h long −0.389R (25) vs short +0.168R (32).
   Shorts beat longs at 1h and 4h only, so "flip `bos` 15m/1h short-only first" loses its
   15m half. `suppress_long = true` STANDS: causal longs are negative on every tier.
5. **`bos` ADR-exempt cells** (`2026-05-17-adr-exempt.md`, `2026-05-21-adr-exempt-mon-fri-weekend.md`)
   — MOVED, and not inert as #949 filed: #400 wrote both into config
   (`signal_watch.toml` `adr_exempt_short = true`; `signal_watch_weekdays.toml` 15m short).
   Re-scored with `gate_audit`'s adr-exempt table on the 2026-10-09 runs: tue_thu 1h short
   ADR-late slice **+0.804R, n=15** (filed +0.42R, n=80); tue_thu 15m short −0.789R, n=22;
   mon_fri 15m short **−0.094R, n=16** (filed +0.104R, n=138). All INSUFFICIENT under n=30.
6. **Stored ratings and DSR** — MOVED for DSR, STANDS stale for one cell. Every `bos` DSR now
   comes from a clean 2026-10-09 pool and none exceeds 0.10. `signal_watch_all` `bos/4h` is
   still rated partly from ETHUSDT's pre-fix 2026-05-21 run (7 trades at −1.0R), because the
   post-fix ETH run closed 0 trades and `select_rated_run_ids` keeps only `closed_trades > 0`.
   The stored `bos/4h/long` row (★1, −0.446R) carries it.
7. **Not re-run by #949.**
   - **H14** (`2026-08-04-h14-coinbase-premium-state-tag.md`) — STANDS: all 10 primary
     `prem_adj` cells INSUFFICIENT on 148,303 deduped trades, as amended 2026-08-13.
   - **H15** (`2026-08-04-h15-usdjpy-carry-unwind.md`) — MOVED. Every forward-panel and
     ledger cell still reads NO-EDGE or INSUFFICIENT, but the near-miss ledger cell
     `magnitude/yen_weak/long` (n=35 days) now reads −0.378R on [−0.575, −0.154], Holm p
     0.008, PBO 0.000 and DSR **0.9496**. The filed values were −0.332R, p 0.027, PBO 1.000
     and DSR 0.878. Six of seven legs pass. It is the secondary panel, the cell is post-hoc,
     and it misses on deflation, so this is not an AVOID.
   - **ST28 multi-regime** (`2026-08-12-multi-regime-validation.md`,
     `2026-08-14-st28-multi-regime-powered-null.md`) — STANDS, with a different fourth cell.
     On the regenerated legs, cells 1, 2 and 4 reproduce the filed values to the digit
     (`cvd_divergence` short +0.1303, `eqh_eql` short +0.1065, `engulfing` short +0.0741,
     same n), so the regeneration matches the original sweep. Causal `bos/15m/long` pools
     **+0.0812R** (n 1,074 / 1,047), so Phase 1 now pre-registers it ahead of
     `fib_golden_zone/15m/long` (+0.0738R); the pre-fix rows had kept it out. It reads Δ
     +0.112R, t +1.13, INSUFFICIENT. `eqh_eql/15m/short` is unchanged at Δ +0.1577R, t +2.33,
     short of the Bonferroni 2.498. No cell detects regime dependence.
   - **ADR threshold** (`2026-05-17-adr-suppress-threshold.md`) — not re-runnable as filed.
     It replayed candidates below 0.80 on sweeps run at 0.80; every config now runs at the
     tightened threshold, so the trades in `[new, 0.80)` are no longer generated. Its
     population was pooled across about 20 detectors, and none of its three tightenings was
     decided by a `bos` or `liquidity_sweep` cell, so the leak cannot have set its direction.
     It STANDS unverified.

## Method

- **Snapshot**: `analytics.db` copied 10:14:53 UTC, outside the signal-watch lock minutes,
  with no WAL present.
- **Leak classifier**: a row is causal if HEAD's detector, run with default params on the
  snapshot's full `ohlcv`, emits its `(signal_time, direction)`; otherwise it is leaked.
- **Tools**: each verdict's own tool, run on the snapshot through the migrated loader:
  `indicator_condition_audit.py --source backtest --timeframes <tf>`,
  `premium_state_audit.py --series prem_adj --source backtest`,
  `carry_unwind_audit.py --source ledger`, and `gate_audit.py`'s `build_audit_table` for
  items 3 and 5 (the CLI skips `liquidity_sweep` because its run-level `volume_suppress` is
  set, so the table was called with that strategy named off).
- **ST28 legs**: regenerated into a second snapshot copy from a scratch working directory
  (`DEFAULT_DB_PATH` is relative) with `buibui backtest --symbols BTCUSDT ETHUSDT SOLUSDT
  --timeframes 15m --since 2021-01-01 --save`: 57 combos, 861,765 trades, 29 minutes. The
  sweep crashed afterwards on a cp1252 encode while printing its table, after every run had
  saved. `multi_regime_study.py` then read that copy through the loader.
