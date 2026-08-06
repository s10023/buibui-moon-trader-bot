# Design: `/card` capital resolution + SL-vs-invalidation coherence

**Date:** 2026-08-06
**Status:** Approved — implementation pending
**Supersedes:** the Task 2b bullets in `docs/plans/next-conversation-prompt.md` (gitignored)

## Problem

`SizingConfig.capital` (`portfolio/sizing.py:21`) defaults to `10_000.0` and nothing
overrides it on the `/card` path. It is used as a stand-in for "the account" by two
independent consumers, so one stale constant produces two wrong answers:

| Consumer | Site | Consequence |
| --- | --- | --- |
| Position sizing | `card/card.py:326` — `risk_usd = sizing.capital * r_adm` | printed risk fraction understates real risk |
| Daily circuit breaker | `card/state.py:272` — `daily_r = pnl / (sizing.capital * sizing.r_base)` | `daily_loss_limit_r` cannot fire at its intended severity |

Measured against the documented account readings — equity `2000.00` (today's
`daily_check.py` run) and `1201.33` (PR #572, the figure the account "never left") —
the constant is **5× to 8.3× too large**. A card printing `risk $25.00 (0.25% of
capital)` is really risking **1.25%–2.1%**, and three concurrent cards are
**3.7%–6.2%** at risk rather than 0.75%.

The circuit-breaker half is the more serious of the two and was previously unrecorded.
With `r_base = 0.0025`, the R unit is `capital × r_base` = **$25** at the constant but
**$3.00–$5.00** against real equity. A genuine −2R day therefore computes as roughly
−0.24R to −0.4R and passes the `daily_loss_limit_r = -2.0` gate. **The guard is not
mis-labelled; it is off.**

### Three corrections to the previously filed description

Recorded because each was filed as fact and is false — the standing rule is that a
filed artifact is a hypothesis until re-derived from the caller.

1. **A capital override already exists end-to-end.** `CardConfig.sizing_toml`
   (`card/config.py:71`) is read at `cli/card.py:147` via
   `SizingConfig.from_toml(...)`, which parses a **`[portfolio]`** table. The filed
   note said to "set `capital` in a `[card]` TOML"; there is no such key, and
   `CardConfig.from_toml` raises on unknown keys, so following that note verbatim
   fails loudly. Changing the number today needs **zero code**.
2. **The card already fetches real equity and ignores it.**
   `AccountState.equity_usd` is populated at `card/state.py:273` from
   `account_provider.equity_usd()` and is never consulted by sizing. The defect is not
   a missing input; it is an available input left unused.
3. **The prompt version is `card-v3`** (`card/prompt.py:10`), not `card-v4` as filed.

### The RR claim is wrong, and the rule must not be built on it

The filed description says the printed `RR(tp1) 2.65` was "flattered" by a stop sitting
beyond the invalidation level. Arithmetically it was not: risk-per-unit is
`|entry − sl|`, so moving the stop *further* from entry makes the denominator
**larger** and RR **smaller**. The worked example computes
`(64634 − 63847) / (63847 − 63550) = 787 / 297 = 2.65`, and a stop at the stated
invalidation would make risk-per-unit `0` and RR undefined, not smaller.

The real defect is unchanged in severity but different in kind: **the position is held
through the point at which the card itself declares the thesis dead.** The card's prose
read *"breaks below PDL 63847 and fails to reclaim it"* while the stop sat at `63550`,
so 297 points of confirmed break-of-structure were absorbed by design. The rule below
is written on that basis. Building it on the RR claim would produce a check that
measures the wrong quantity.

## Non-goals

- Changing how RR is computed. RR stays anchored to `sl`, which is the actual money at
  risk.
- Auto-trading, or any change to `r_base`, the overlay, or the XS executor.
- The `/card` vs signal-watch DuckDB lock contention (separate item).

## Component 1 — shared capital resolution

A pure helper in `portfolio/sizing.py`, beside the config it resolves:

```python
def resolve_capital(cfg: SizingConfig, equity_usd: float | None) -> tuple[float, bool]:
    """Capital to size against, and whether it came from live equity."""
```

Live equity wins when it is a finite number greater than zero; otherwise the configured
`cfg.capital`. Returning the source as a `bool` rather than logging inside keeps the
helper pure and lets both call sites decide how to surface it.

Both consumers call this one helper with the same `equity_usd`, so they cannot
disagree:

- `card/state.py` — `daily_r` divides by `resolve_capital(...)[0] * r_base`
- `card/card.py` — `risk_usd` multiplies `resolve_capital(...)[0]`

`post_pass` reads equity from `state.account.equity_usd`, which is `None` whenever the
account is absent. That happens on a pinned `--as-of` run (the account is unpinnable and
deliberately omitted) and on a credentials/network failure. Both fall back to config
capital; the fallback is surfaced as a warning, never silent.

**Degenerate inputs must fall back, not propagate.** `equity_usd` of `0.0`, a negative,
`NaN`, or `inf` all take the config path. A zero would otherwise size every card to zero
and read as a lot-size veto; a `NaN` would poison `risk_usd`, `risk_frac`, and
`notional_usd` without raising.

## Component 2 — record the capital behind every card

`FinalCard` gains `capital_used: float | None` and `capital_source: str | None`
(`"live_equity"` | `"config"`), rendered and written to the ledger.

This is load-bearing rather than cosmetic. Once capital is live, `risk_frac` varies
run-to-run for reasons outside the card, so a ledger row that records the fraction but
not the capital behind it cannot be interpreted after the fact. This is the same lesson
as `last_run.capital_override` in the xsmom executor, where an unrecorded pin made a
poisoned high-water mark undiagnosable.

Both fields are `None` on a VETOED or NO_TRADE card, matching the existing convention
that `size_units` / `risk_usd` / `rr_tp1` are cleared when nothing is sized.

## Component 3 — `invalidation_level` and the SL coherence rules

### Schema

`TradeCard` gains `invalidation_level: float | None`, emitted by the model beside the
existing free-text `invalidation`. `_SCHEMA` and the rubric in `card/prompt.py` gain the
field, and `PROMPT_VERSION` moves `card-v3` → `card-v4`.

**Required for a TRADE verdict**, enforced in `validate_card_obj` alongside the existing
`_PRICE_KEYS` checks. An optional field the model may omit yields a check that silently
never fires — the pre-committed-gate-leg vacuity that CLAUDE.md records as having
shipped undetected in H8. Making it a validation error means a non-compliant model fails
loudly instead.

Parsing a price out of the free-text prose was considered and rejected: the prose is
model-authored, so a regex would silently miss or misread levels, producing exactly the
"check that can pass while the thing it guards fails" failure mode.

### Rule 1 — coherence

For a long, `invalidation_level` must be strictly below `entry`; for a short, strictly
above. Otherwise no stop can satisfy both the existing side rule (`sl < entry` for a
long) and Rule 2, so the card is internally contradictory. **VETO.**

This is the rule the worked example actually trips: entry `63847` *is* the invalidation
level (both are the PDL), so the trade is entered exactly where its own thesis dies.

### Rule 2 — stop placement

For a long, `sl` must not fall below `invalidation_level` by more than
`tol × (entry − invalidation_level)`; mirrored for a short. **VETO** on breach.

Stated as the exact inequality each rule vetoes on, so no implementation has to infer
the boundary:

```text
long:   veto iff  sl < invalidation_level - tol * (entry - invalidation_level)
short:  veto iff  sl > invalidation_level + tol * (invalidation_level - entry)
```

New `CardConfig.sl_invalidation_tol_frac: float = 0.25`. A buffer past a level is
legitimate practice against wick hunts, so the allowance scales with the trade's own
structural distance rather than being an arbitrary absolute that means different things
on BTC and SOL. This is the `bar`-units lesson from H15 applied at design time.

Rule 1 runs first; when it vetoes, Rule 2 is not evaluated, because a negative
`entry − invalidation_level` would make the tolerance negative and the comparison
meaningless.

## Testing

Unit, with no network — mocks passed directly, per the existing suite convention.

**`resolve_capital`:** live equity wins over config; `None` → config; and each
degenerate input (`0.0`, negative, `NaN`, `inf`) → config. Enumerate the input class
rather than spot-check it — the LOT_SIZE defect stood while every spot-check passed.

**Circuit breaker:** a `daily_pnl_usd` that breaches `daily_loss_limit_r` against live
equity but not against the `10_000.0` constant. This is the positive control: it fails
on today's code and passes after the change, so it proves the stimulus is live rather
than asserting an invariant that already held.

**`post_pass`:** `risk_usd` / `risk_frac` computed against live equity; `capital_used`
and `capital_source` recorded on a TRADE and `None` on a VETO; the config-fallback
warning present when the account is absent.

**Invalidation rules:** Rule 1 veto (long with level ≥ entry, short with level ≤ entry);
Rule 2 veto (stop beyond tolerance); a within-tolerance stop passing; both directions
for each. Schema: a TRADE omitting `invalidation_level` is a validation error.

**Regression goldens:** `make test-regression` is expected **unmoved** — the card is not
in the backtest pipeline — and this is to be verified by running it, not assumed. Run it
via `make test-regression`, never a bare `pytest`, which reports the three ~97s golden
backtests as timeout failures under the global 30s cap.

## Definition of Done

`make lint-py`, `make typecheck`, `make test`, `make test-regression` — each run and
each result stated plainly.

## Operational note

Behaviour changes for anyone running `/card` against a funded account: printed risk will
rise to the true figure. That is the point of the change, but it means a card generated
after this lands is not comparable to one generated before it at the same `risk_frac`.
The `capital_source` field in the ledger is what makes the two distinguishable.
