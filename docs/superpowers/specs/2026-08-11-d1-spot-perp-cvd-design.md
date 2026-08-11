# D1 — Spot-perp CVD divergence sleeve (design + pre-committed gate)

**Status:** design, pre-registered. No result exists yet.
**Date:** 2026-08-11.
**Item:** SoT D1 (intake — new data axes). Thesis rows: `docs/plans/thesis-inbox.md`
2026-07-03 (@JordiCharts), 2026-07-27 (@PILTR_XBT).
**Memory:** `project_spot_perp_cvd_data_axis.md`.

Everything below the line "measured" or "verified" was read from source or probed on
2026-07-31 / 2026-08-11 and is cited with a path. Everything else is a commitment made
*before* seeing a result, which is the only reason this document is worth writing.

## 1. The question

Does venue-split order flow — Binance **spot** taker imbalance against Binance **perp**
taker imbalance — carry a tradeable edge that is *decorrelated from XS-momentum*?

The pundit form of the claim ("who is doing the buying decides whether a move
continues") is not decidable at the resolution it was stated. The decidable form is:

> Build a continuous forecast from the spot-perp taker-imbalance difference, run it
> through the two book constructions this repo already owns, and take a three-leg gate
> verdict on each pre-registered trial.

**Success metric:** a gate-decidable verdict per trial — `DSR >= 0.95 AND PBO <= 0.5
AND boot_lo > 0` — plus a `corr_to_xsmom` stamp that says whether a passing book is a
*second* edge or a restatement of the first. A clean NO is a success by this metric. An
undecidable result (NaN PBO, under-powered panel) is the only real failure.

## 2. Why this axis, after H14 and H15 both returned clean NOs

The last two new-axis attempts failed, and it matters that this one differs on both
axes that made them fail.

| | H14 Coinbase premium | H15 USD/JPY carry | D1 spot-perp CVD |
| --- | --- | --- | --- |
| input | cross-venue **price** | cross-asset **price** | **order flow** |
| construction | state-tag conditioning | state-tag conditioning | standalone forecast |
| family record | 0-for-6 | 0-for-6 | 2-of-5 clear the gate |

Two claims follow, and neither is a promise of an edge:

1. **The input is genuinely new.** `analytics/` ingests Binance USDT-M perp klines and
   nothing else. Taker imbalance on a *second venue* is not a re-slice of held OHLCV.
   Roughly eight audits have converged on "the next edge needs genuinely new data";
   this is the cheapest untested candidate that satisfies that.
2. **The construction is the one that has produced a winner here.** State-tag
   conditioning is 6-for-6 NO in this repo. Pouring new data through the construction
   that has never once returned an edge would be a weak test of the data. Of the five
   sleeves, **two clear the gate** — `xsmom` (+1.375) and `combine` (+1.145) — but
   `combine` is built *on* `xsmom` and is Sharpe-dominated by it, so the count of
   independent edges the sleeve construction has produced is **one**. That is a weak
   record. It is not zero, and the one that cleared is the book that carries capital.

**The prior is still low**, and the memory file says so plainly. @PILTR_XBT's own
supporting read leans on an L2 depth panel that remains unobtainable
(`project_liquidity_heatmap_gap.md`), so only the CVD leg is testable — a negative
result falsifies our half, not his thesis. Write that in the verdict either way.

### What this is NOT

It is not the frozen `cvd_divergence` detector. That one
(`analytics/strategies/cvd_divergence.py:12`) reads a **single** flow series against
price and fires on swing-high/swing-low divergence pairs. This reads **two independent
order-flow populations against each other**. Different primitive, different unit,
different consumer. No change is proposed to that detector.

## 3. Data — measured, not assumed

### The perp half needs no new ingestion

`taker_buy_volume` is already a column on the `ohlcv` table
(`analytics/store/schema.py:18`, with a migration guard at `schema.py:29` for DBs
predating it), and `analytics/data_fetcher.py:82` already pulls kline field 9 into it.
Perp CVD is available today at zero ingestion cost.

### The spot half is keyless and free

`GET https://api.binance.com/api/v3/klines` — no key, HTTP 200, probed 2026-07-31.
Field 9 is `taker_buy_base_asset_volume`, the exact quantity the perp path already
stores. Field 5 is `volume`.

**`analytics/venue_fetch.py:73 fetch_binance_spot_daily` already calls this endpoint**
for H14, with working pagination and a stale-cursor guard (`venue_fetch.py:90`) against
a misbehaving response that would otherwise loop forever. It keeps only `row[4]`
(close) and discards the rest. The new fetcher is a sibling of proven code, not a
greenfield HTTP client.

### Universe coverage is 23 of 25

`config/universe.toml` holds 25 perps. Mapping to spot:

- **21 map straight across** (`BTCUSDT` → `BTCUSDT`, etc.).
- **`1000PEPEUSDT` → `PEPEUSDT`.** The perp carries a 1000x multiplier in its name. The
  imbalance primitive in §4 is a *ratio*, so the multiplier cancels and no rescaling is
  needed — but the symbol map must be explicit, never derived by string-stripping.
- **`TONUSDT`** returns klines but is **not** in exchangeInfo's TRADING set. Check its
  status at fetch time and exclude it rather than silently trusting the klines.
- **`HYPEUSDT` and `VVVUSDT` have no spot pair at all.** They are dropped.

**This is why §7 re-runs the XS-momentum benchmark on the same 23.** Its published
+1.375 Sharpe is a 25-symbol number; comparing a 23-symbol CVD book against it directly
would attribute a universe difference to the signal.

### Daily bars are information-complete, and this kills the expensive option

CVD delta over one day, computed from intraday bars, is:

```text
sum_i (2*tbv_i - vol_i)  =  2*sum_i(tbv_i) - sum_i(vol_i)  =  2*tbv_day - vol_day
```

The daily bar's taker-buy field **is** the exact intraday sum. So a daily-frequency
forecast built from 1d klines is identical to one built from 15m klines and
re-aggregated — not an approximation of it. Only the intraday *path* is lost, and no
daily sleeve consumes it.

Consequence: ingestion is ~23 symbols x ~2,500 daily bars ~= 57k rows, not the ~5.6M
rows a 15m backfill would cost. **Do not re-open this as a resolution question**
without first refuting the identity above.

**Caveat, stated because it bounds the result and not because it blocks it:** daily
taker imbalance is a coarse flow measure. If the real information is intraday
sequencing — spot selling *into* a perp-led rally within the session — this design
cannot see it, and a NO here does not rule that out. Say so in the verdict.

### History

Spot BTCUSDT 1d reaches 2017-08-17; the perp series starts 2019. The **paired** series
necessarily starts where both exist, so the usable window is the perp window. This
buys no extra history, only a second venue over the existing one.

### Storage

A new `spot_ohlcv` table, same shape as `ohlcv`. **Not** a `venue` column on `ohlcv`:
that changes its primary key and every read on the live signal path, and the 15-minute
timer runs the working tree and holds `analytics.db`. Isolation is the point.

## 4. The primitive — pre-registered

Per symbol, per day, per venue:

```text
imb_v = (2 * taker_buy_volume_v - volume_v) / volume_v        # in [-1, 1]
x     = imb_spot - imb_perp                                    # the disagreement
```

`imb_v` is the signed taker-flow share: `+1` = every taker was a buyer, `-1` = every
taker was a seller, `0` = balanced. It is **scale-free by construction**, which is what
makes the rest simple: no cross-symbol volume normalisation, no cross-era normalisation
as volumes grow, and the `1000PEPEUSDT` multiplier cancels.

`x` is the quantity the thesis names: positive when spot takers are more aggressive
buyers than perp takers.

### Sign convention — committed a priori

**LONG when spot leads perp** (`x > 0`).

The reasoning, recorded now so it cannot be retrofitted: spot buying against perp
selling reads as balance-sheet demand, while a perp-led push with spot absent reads as
short-covering or leveraged chasing — flow that must later unwind. This is
@PILTR_XBT's stated direction.

**After the fact both signs are narratable**, which is exactly why this is written
before any result. If the measured edge runs the other way, that is an inverted
finding to be reported as such under §8's negative-direction rule — not a sign flip to
be quietly adopted.

### The forecast

```text
f = clip( fdm * EWMA_span(x) / std_trail(EWMA_span(x)), -cap, +cap )
```

This puts `f` in the repo's Carver forecast units (`cap = 20.0`, `fdm = 1.25`), which
is what `analytics/xsmom/book.py:67 xs_leverage` expects. The forecast matrix is
columns = symbols, index = union daily index, NaN during warm-up — the same contract
`xs_forecasts` produces (`book.py:27`), because it feeds the same socket.

### Causality — committed, and asserted on the position

The forecast for day *d* uses data through the **close of day** *d*; the position it
implies applies to the *d+1* return.

**The guard asserts on the position matrix, not on book returns.** Perturbing
`close_d` legitimately changes the day-*d* book return, because that return is
`leverage_d * r_d` and `r_d` depends on `close_d` by construction. P3's original
causality test asserted on returns and was **unsatisfiable as literally written**;
it was amended 2026-08-06 to assert on the position, which holds to machine zero. This
spec inherits the amended form. Never "repair" working P&L to satisfy the old wording.

## 5. The books — two shapes, both declared now

**Terminology, fixed here to avoid a collision:** a **shape** is a book construction
(there are two, below); a **trial** is one member of the DSR/PBO multiple-testing
family (there are five per shape, §7). Ten trials across two shapes.

The primitive is built **once** and fed to both constructions. Both are declared
before any result, so that reaching for the second after the first fails is not
available as an unrecorded degree of freedom.

**Shape A — cross-sectional.** Rank the 23 symbols by `f`, demean across the
cross-section, run the dollar-neutral long-short book via
`analytics/xsmom/book.py:114 run_xs_backtest(closes, fundings, cfg, forecasts=F)`.
That parameter already exists and its docstring states it "injects a sibling sleeve's
raw forecast matrix in place of the EWMAC path; `None` is byte-identical" — the socket
`combine/` uses, described in CLAUDE.md as "a validated socket awaiting a comparably
strong second edge". Strips market beta. Costs, funding, the causal 20%-vol governor
and the capacity stress (`analytics/xsmom/execution.py:106 run_xs_with_costs`) all come
free with the socket.

**Shape B — time-series.** Per symbol, `f` drives a long/flat/short position through
`analytics/forecast/book.py:72 run_forecast_backtest` with the same vol targeting and
costs. This is
the claim *as the pundits stated it* — that one asset's venue disagreement predicts its
own continuation. It carries market beta. EWMAC failed the gate at +0.36 through this
path, but that is one datapoint about trend, not about time-series as a shape.

Neither is favoured in the decision rule. Both are reported, win or lose.

## 6. The unit of observation

One **symbol-day book return**, aggregated to a daily portfolio return by the book
construction. This is the unit `evaluate_xs` already consumes.

**Book-day rows must NOT be deflated for cross-sectional correlation.** They are
already aggregated across instruments. The `effective_independent_series` deflator
(`analytics/forecast/attribution.py`) applies only to any *per-symbol* cut this study
takes — and it must be applied there, because mean pairwise correlation of
per-instrument net returns on this universe is **0.315**, so 25 perps carry the noise
reduction of **2.92** effective independent series, not 25. A naive t over pooled
symbol-days inflates ~2.92x. Any per-symbol table in the verdict carries both
`t_stat` (deflated) and `t_stat_naive`, as `RegimeCell` already does.

## 7. The gate — pre-committed

### The trial family is mandatory, not a choice

`analytics/xsmom/report.py:82 evaluate_xs` computes PBO with `cscv_pbo` over a matrix
of trial returns, and returns `NaN` when there are fewer than 2 trials or fewer than 28
aligned observations (`report.py:102`). **`NaN` fails `passes_gate`.** A single-config
run is therefore not merely weak evidence — it is undecidable. The family is a
requirement of the harness.

**Family, per shape: four single-span forecasts plus their combination = 5 trials.**
Times two shapes = **10 trials total, declared here, before any result.**

The four spans are **8, 16, 32, 64 days** — the *fast* legs of `_DEFAULT_SPEEDS`
(`analytics/forecast/config.py:16`, which holds `(8,32,5.3) (16,64,3.75) (32,128,2.65)
(64,256,1.91)`). Two notes, because "mirroring `_DEFAULT_SPEEDS`" would otherwise be
ambiguous:

- **EWMAC's speeds are fast/slow crossover *pairs*; `f` here takes a single smoothing
  span.** There is no crossover to mirror, so the pairs cannot be inherited whole. The
  fast legs are taken and the slow legs discarded.
- **The fast legs, not the slow ones, and the reason is a priori.** Taker imbalance is
  a bounded, noisy, day-to-day quantity with no drift; a 256-day EWMA of it is close to
  a constant and would carry almost no signal. 8–64 days spans fast to slow for a flow
  series. This is a judgement about the primitive, made before any result — not a
  choice between measured outcomes.

### No parameter tuning

Every parameter is **inherited**, not chosen for this study:

| parameter | value | source |
| --- | --- | --- |
| `vol_span` | 32 | `ForecastConfig` default |
| `fdm` | 1.25 | `ForecastConfig` default |
| `cap` | 20.0 | `ForecastConfig` default |
| `vol_target_annual` | 0.20 | `ForecastConfig` default |
| `gov_window` / `g_min` / `g_max` | 64 / 0.5 / 1.5 | `ForecastConfig` default |
| `fee_pct` / `slippage_pct` | 0.0005 / 0.0002 | `ForecastConfig` default |
| smoothing spans | 8 / 16 / 32 / 64 | the fast legs of `_DEFAULT_SPEEDS`; they *are* the trial family |

The only new degrees of freedom are the spans, and they are declared as trials rather
than swept. **If this study later sweeps any parameter above, the trial count changes
and the DSR haircut must change with it** — record that in the verdict rather than
leaving the stamp stale.

### The legs

**Three legs: `DSR >= 0.95 AND PBO <= 0.5 AND boot_lo > 0`**, via
`analytics/research_guards/gate.py passes_gate` — called, never re-inlined, and the
thresholds are never restated anywhere else. All five existing sleeves implement
exactly this.

`min_trl` and `corr_to_trend` are **reported stamps, not legs**. Coding `corr_to_trend`
as a pass condition would fail the sleeve that carries capital at its measured +0.37.
`min_trl` against a non-zero target asks "can I confirm Sharpe >= 1", a far harder
question than "is there an edge". **Do not quote a four-leg gate.**

### Two additions specific to this study

**`corr_to_xsmom` — a new stamp, not a leg.** `evaluate_xs` already takes
`trend_returns` and reports `corr_to_trend`. For this study the decisive comparison is
against the **deploy core**, not the shelved trend sleeve: a CVD book correlating ~0.9
with XS-momentum is a restatement of the first edge, not a second one, and the combine
socket's entire value is decorrelation. Both stamps are reported.

It is deliberately **not** a gate leg. Picking a cutoff now, with no knowledge of the
distribution, would be arbitrary — and a hard threshold on an unknown distribution is
the shape that made `corr_to_trend` a near-miss disqualifier for the deploy core. It
enters the verdict as a judgement in §8, with the number printed.

**Benchmark re-run on the same 23 symbols.** XS-momentum is re-run on the CVD
universe so the head-to-head is like-for-like. The published +1.375 stays the
25-symbol number and is not compared against directly.

## 8. Pre-committed decision rule

| outcome | verdict |
| --- | --- |
| a trial clears all three legs, **and** `corr_to_xsmom` is low | **candidate second edge** — next step is the combine socket, not a deploy |
| a trial clears all three legs, `corr_to_xsmom` is high | **not a second edge** — reported as a restatement of XS-momentum |
| no trial clears | **SHELVED** — verdict doc written, do not rebuild |

No outcome deploys capital. Clearing the gate earns a place in the combine socket and
a second study, matching how XS-momentum itself was handled.

**A shelved verdict is a result, not a failure.** It goes in `docs/audits/` with the
same weight as a pass, and CLAUDE.md's sleeve-verdict table gets a row so nobody
rebuilds it — the fate of `carry/` and `xsrev/`.

### The negative-direction rule

If the honest finding is that the measured edge runs **opposite** to §4's pre-registered
sign, that is an inverted finding, and reporting it requires folding the Sharpe to
`abs()` for **both** the target and the trial values before computing DSR.

`deflated_sharpe_ratio` and `min_track_record_length` are **directional** — they answer
"is this *positive* performance credible". DSR of a raw negative Sharpe collapses to
~0 and MinTRL is `inf`, so a negative verdict gated on either is **structurally
unreachable**: the study would report "no effect found" no matter what the data said.
This shipped in H8 and H14 and stood for weeks in H8, where a reliably-negative cell
scored DSR 0.0000 against 0.9980 for its mirror-image positive cell.

**Disclose the cost when doing it:** folding to magnitude shrinks trial dispersion in a
mixed-sign family, so the gate becomes marginally *more permissive* than the signed
form. The bias runs toward more passes, never fewer.

## 9. Architecture

New package `analytics/cvd/`, mirroring the existing sleeve layout:

| file | responsibility | depends on |
| --- | --- | --- |
| `fetch.py` | daily spot klines keeping `volume` + field 9; injectable `get`; symbol map; TRADING-status check | `venue_fetch` paging shape |
| `imbalance.py` | `imb_v` and `x` from paired bars; NaN handling for missing venue days | pandas only |
| `forecast.py` | `x` -> capped, vol-scaled forecast matrix; the causality contract | `ForecastConfig` |
| `replay.py` | both book shapes + the 5-trial family per shape | `xsmom.book`, `forecast.book` |
| `report.py` | metrics, `passes_gate`, `corr_to_xsmom`, benchmark-on-23 | `research_guards` |

Store: a `spot_ohlcv` migration in `analytics/store/`, reusing `_upsert` from
`analytics/store/_common.py`. **That function's explicit `conn.register` /
`conn.unregister` in try/finally is load-bearing** — never switch to the implicit
replacement scan (malloc heap corruption) and never drop the try/finally.
`DEFAULT_DB_PATH` is imported from a re-export, never redefined.

Driver: `tools/cvd_audit.py`, following `tools/xsmom_audit.py`. Verdict:
`docs/audits/2026-<MM>-<DD>-d1-spot-perp-cvd.md`.

**Nothing in this package is imported by the live signal path, the alert formatter,
the star ratings, or the card.**

## 10. Testing

- **No network in any test.** Fetchers take an injectable `get` exactly as
  `venue_fetch.http_get_json` does; tests pass a fake. Assert the **shape of the
  request** (URL, params, paging cursor advance), not merely that a fake was called —
  a faked subprocess or fetcher that is never checked against the real command shape
  is a blind spot this repo has been bitten by.
- **DuckDB in-memory** (`duckdb.connect(":memory:")`) for every store test. Never touch
  the real `analytics.db`.
- **Every path passed explicitly.** All nine CLI default paths are gitignored *and*
  present on this machine, so a default-path test passes locally with 100% reliability
  and fails on every fresh clone. Verify hermeticity by **moving the file aside**.
- **The causality guard is proven by injecting a violation.** A guard that has never
  been shown to fail is not known to cover anything — P3's own causality guard passed
  while being vacuous. Write the injection test first.
- **Identity test for §3's daily-completeness claim.** Fetch one symbol-week at 15m and
  at 1d, aggregate the 15m taker-buy and volume to daily, and assert the imbalance
  matches to floating-point tolerance. This is the test that lets a future session
  trust the cheap ingestion path instead of re-deriving it.
- **Enumerate the input class, never spot-check**, wherever a numeric edge case is in
  play. The `round_down_to_step` defect passed every spot-check while broken.

Gates: `make lint-py`, `make typecheck`, `make test`. **`make test-regression` is not
required** — this diff touches none of the backtest surface (`analytics/backtest/`,
`analytics/strategies/`, `analytics/signal_config.py`, `config/*signal_watch*.toml`,
`config/strategy_params.toml`, `tests/fixtures/`, `poetry.lock`). State which branch
was taken.

## 11. Deliverables

1. `analytics/cvd/` package + `spot_ohlcv` store migration + tests.
2. `tools/cvd_audit.py` driver.
3. A backfill of daily spot bars for the 23 paired symbols.
4. A verdict document in `docs/audits/`, written whichever way the gate falls, carrying:
   the three-leg stamp per trial, `corr_to_xsmom`, the benchmark-on-23 comparison, the
   declared trial count, and the intraday-path caveat from §3.
5. A row in CLAUDE.md's sleeve-verdict table, so a shelved result is not rebuilt.
6. SoT + memory updated with the verdict.

## 12. Out of scope

- No new detector, and no change to `cvd_divergence`.
- No live wiring: nothing feeds alerts, ratings, sizing, or the card.
- No intraday ingestion — see §3's identity, and refute it before re-opening.
- No parameter sweep. Sweeping changes the trial count and the DSR haircut with it.
- No OI leg. @TraderMorin's OI/CVD absorption row is a *separate* thesis needing no new
  data; folding it in here would multiply the cell count and confound two claims.
