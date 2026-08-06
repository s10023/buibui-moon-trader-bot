# `/card` Capital Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/card` size positions and scale its daily circuit breaker against the
account's real equity instead of a hardcoded `10_000.0`, and record on every card which
capital produced its numbers.

**Architecture:** One pure helper, `resolve_capital`, added to `portfolio/sizing.py` and
called from the two places that currently read `SizingConfig.capital` as a stand-in for
the account (`card/state.py` for `daily_r`, `card/card.py` for `risk_usd`). Both call
sites pass the same `equity_usd`, so they cannot drift. `FinalCard` gains
`capital_used` / `capital_source`, which reach the JSONL ledger for free because
`append_ledgers` writes `asdict(final)`.

**Tech Stack:** Python 3.13, dataclasses, pytest + unittest.mock, ruff, mypy strict,
Poetry.

**Spec:** `docs/superpowers/specs/2026-08-06-card-capital-and-invalidation-design.md`

## Global Constraints

- Every function needs type annotations including the return type; test methods use
  `-> None`. mypy runs in strict mode (`disallow_untyped_defs = true`).
- Tests must make no network calls. Pass `MagicMock` dependencies directly.
- **Component 3 of the spec is dropped.** Do not add `invalidation_level`, do not add
  `sl_invalidation_tol_frac`, do not touch `card/prompt.py`, and leave `PROMPT_VERSION`
  at `card-v3`.
- Definition of Done, all four run and each result stated plainly: `make lint-py`,
  `make typecheck`, `make test`, `make test-regression`.
- Never diagnose golden drift from a bare `pytest tests/` — the global 30s timeout
  reports the three ~97s golden backtests as failures. Use `make test-regression`.
- Do not reformat unrelated files. `make lint-py` runs `ruff format .` across the repo
  including Markdown python fences; if it rewrites files outside this change, leave
  them out of the commit.

---

### Task 1: `resolve_capital` pure helper

**Files:**

- Modify: `portfolio/sizing.py` (add after `position_size`, before `_STEP_SNAP_REL_TOL`)
- Test: `tests/test_portfolio_sizing.py`

**Interfaces:**

- Consumes: `SizingConfig` (already in this module).
- Produces: `resolve_capital(cfg: SizingConfig, equity_usd: float | None) -> tuple[float, bool]`
  returning `(capital, used_live_equity)`. Tasks 2 and 3 both import this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_portfolio_sizing.py`, and add `resolve_capital` to the existing
`from portfolio.sizing import ...` line at the top of that file:

```python
class TestResolveCapital:
    def test_live_equity_wins_over_config(self) -> None:
        cfg = SizingConfig(capital=10_000.0)
        capital, used_live = resolve_capital(cfg, 1201.33)
        assert capital == pytest.approx(1201.33)
        assert used_live is True

    def test_none_equity_falls_back_to_config(self) -> None:
        cfg = SizingConfig(capital=10_000.0)
        capital, used_live = resolve_capital(cfg, None)
        assert capital == pytest.approx(10_000.0)
        assert used_live is False

    @pytest.mark.parametrize(
        "equity",
        [0.0, -1.0, -1201.33, float("nan"), float("inf"), float("-inf")],
    )
    def test_degenerate_equity_falls_back_to_config(self, equity: float) -> None:
        # Enumerate the input class rather than spot-check it: a zero would size
        # every card to nothing and read as a lot-size veto, and a NaN would
        # poison risk_usd / risk_frac / notional_usd without ever raising.
        cfg = SizingConfig(capital=10_000.0)
        capital, used_live = resolve_capital(cfg, equity)
        assert capital == pytest.approx(10_000.0)
        assert used_live is False

    def test_tiny_positive_equity_is_honoured(self) -> None:
        # The fallback triggers on invalid, never on merely small: a $50 account
        # is a real account and must not silently size as if it held $10,000.
        cfg = SizingConfig(capital=10_000.0)
        capital, used_live = resolve_capital(cfg, 50.0)
        assert capital == pytest.approx(50.0)
        assert used_live is True
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/test_portfolio_sizing.py::TestResolveCapital -q
```

Expected: collection error — `ImportError: cannot import name 'resolve_capital'`.

- [ ] **Step 3: Write the implementation**

Insert into `portfolio/sizing.py` immediately after `position_size`:

```python
def resolve_capital(cfg: SizingConfig, equity_usd: float | None) -> tuple[float, bool]:
    """Capital to size against, plus whether it came from live equity.

    Live account equity wins whenever it is a usable number; `cfg.capital` is the
    fallback for every other case. Returns the source as a flag rather than
    logging, so this stays pure and each caller decides how to surface it.

    The fallback covers `None` (no account: a pinned `--as-of` run omits it by
    design, and a credentials or network failure leaves it absent too) and every
    degenerate float. Non-finite and non-positive values must NOT propagate: a
    `0.0` sizes every card to zero and reads downstream as a lot-size veto, and a
    `NaN` poisons `risk_usd`, `risk_frac` and `notional_usd` without raising.
    Smallness is not degeneracy — a genuinely tiny account is honoured.
    """
    if equity_usd is not None and math.isfinite(equity_usd) and equity_usd > 0.0:
        return float(equity_usd), True
    return cfg.capital, False
```

`math` is already imported at the top of this module.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/test_portfolio_sizing.py -q
```

Expected: PASS, including the pre-existing tests in that file.

- [ ] **Step 5: Commit**

```bash
git add portfolio/sizing.py tests/test_portfolio_sizing.py
git commit -m "feat(sizing): add resolve_capital — live equity with config fallback"
```

---

### Task 2: Scale the daily circuit breaker off resolved capital

**Files:**

- Modify: `card/state.py:265-276` (the `else:` branch that builds `AccountState`)
- Test: `tests/test_card_state.py`

**Interfaces:**

- Consumes: `resolve_capital` from Task 1.
- Produces: no new public names. `AccountState.daily_r` changes meaning — it is now
  `daily_pnl_usd / (resolved_capital * r_base)` rather than always dividing by
  `cfg.capital * r_base`.

**Why this task exists:** with `r_base = 0.0025`, one R is `$25` at the constant but
`$3.00` at an equity of `1201.33`. A real −2R day computes as −0.24R and passes
`daily_loss_limit_r = -2.0`, so the breaker cannot fire at its intended severity.

- [ ] **Step 1: Update the one existing assertion this intentionally changes**

`tests/test_card_state.py:210-212` currently reads:

```python
        # account: daily_r = -50 / (10_000 * 0.0025) = -2.0
        assert state.account is not None
        assert state.account.daily_r == -2.0
```

`FakeProvider` (`tests/test_card_state.py:128-145`) returns `daily_pnl_usd = -50.0` and
`equity_usd = 9_000.0`, so the resolved R unit becomes `9_000 * 0.0025 = 22.5` and
`daily_r` becomes `-50 / 22.5 = -2.2222…`. Restate it — do not weaken it to a range:

```python
        # account: daily_r = -50 / (9_000 * 0.0025) = -2.2222, resolved off the
        # provider's live equity rather than the 10_000.0 config constant.
        assert state.account is not None
        assert state.account.daily_r == pytest.approx(-2.2222222222, abs=1e-9)
```

This is the only pre-existing assertion in the suite that changes. The hand-built
`AccountState` at `tests/test_card_state.py:81-82` does not go through
`snapshot_market_state`, so its `daily_r=-0.2` is unaffected.

- [ ] **Step 2: Write the new failing tests**

Add to `tests/test_card_state.py` inside `class TestSnapshotMarketState`. Reuse the
file's existing `_NOW_MS`, `_fake_bundle`, `init_schema` and `duckdb` imports.

```python
    def test_daily_r_scales_off_live_equity_not_the_config_constant(self) -> None:
        """Positive control: this pnl breaches the breaker at real equity only.

        -6.0 USD against equity 1201.33 (R unit 3.0033) is -1.998R and breaches
        daily_loss_limit_r -2.0 once rounded off; against the 10_000.0 constant
        (R unit 25.00) the same day is -0.24R and passes. Asserting BOTH halves
        is what proves the stimulus is live rather than that an invariant
        happened to hold anyway.
        """

        class TinyEquityProvider:
            def positions(self) -> list:
                return []

            def daily_pnl_usd(self, start_ms: int, end_ms: int) -> float:
                return -6.0

            def equity_usd(self) -> float | None:
                return 1201.33

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        state = snapshot_market_state(
            conn,
            "BTCUSDT",
            CardConfig(),
            SizingConfig(),
            now_ms=_NOW_MS,
            account_provider=TinyEquityProvider(),
            brief_fn=lambda _conn, cfg: _fake_bundle(cfg.symbols[0]),
            targets_fn=lambda *a, **k: None,
        )

        assert state.account is not None
        assert state.account.daily_r == pytest.approx(-6.0 / (1201.33 * 0.0025))
        assert state.account.daily_r < -1.99
        # the shipped behaviour would have been nowhere near the breaker
        assert -6.0 / (10_000.0 * 0.0025) == pytest.approx(-0.24)

    def test_daily_r_falls_back_to_config_capital_without_equity(self) -> None:
        class NoEquityProvider:
            def positions(self) -> list:
                return []

            def daily_pnl_usd(self, start_ms: int, end_ms: int) -> float:
                return -6.0

            def equity_usd(self) -> float | None:
                return None

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        state = snapshot_market_state(
            conn,
            "BTCUSDT",
            CardConfig(),
            SizingConfig(),
            now_ms=_NOW_MS,
            account_provider=NoEquityProvider(),
            brief_fn=lambda _conn, cfg: _fake_bundle(cfg.symbols[0]),
            targets_fn=lambda *a, **k: None,
        )

        assert state.account is not None
        assert state.account.daily_r == pytest.approx(-0.24)
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
poetry run pytest tests/test_card_state.py -q -k "daily_r or composes_blocks"
```

Expected: `test_daily_r_scales_off_live_equity_not_the_config_constant` FAILS
(`daily_r` is `-0.24`, not `-1.998`), `test_composes_blocks_with_injected_seams` FAILS
(`-2.0` vs the restated `-2.2222`), and
`test_daily_r_falls_back_to_config_capital_without_equity` PASSES already — it pins the
fallback path, which this change must not alter.

- [ ] **Step 4: Write the implementation**

In `card/state.py`, add `resolve_capital` to the existing
`from portfolio.sizing import SizingConfig` import, then replace the `AccountState`
construction so equity is read once, before it is needed:

```python
        try:
            day_start = now_ms - (now_ms % DAY_MS)
            pnl = account_provider.daily_pnl_usd(day_start, now_ms)
            equity = account_provider.equity_usd()
            capital, _used_live = resolve_capital(sizing, equity)
            # Guard the divisor rather than trusting it: r_base is operator-set
            # and a zero here would kill state building outright.
            r_unit = capital * sizing.r_base
            account = AccountState(
                positions=account_provider.positions(),
                daily_pnl_usd=pnl,
                daily_r=pnl / r_unit if r_unit > 0.0 else 0.0,
                equity_usd=equity,
            )
        except Exception as exc:
            health.append(f"account: {exc}")
```

Also update the `AccountState.daily_r` field comment at `card/state.py:45` from
`# daily_pnl_usd / (capital * r_base)` to
`# daily_pnl_usd / (resolved capital * r_base); see portfolio.sizing.resolve_capital`.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
poetry run pytest tests/test_card_state.py -q
```

Expected: PASS, all of them. Any *other* pre-existing failure means something beyond
`daily_r` moved — investigate rather than restating the assertion.

- [ ] **Step 6: Commit**

```bash
git add card/state.py tests/test_card_state.py
git commit -m "fix(card): scale the daily circuit breaker off real equity"
```

---

### Task 3: Size off resolved capital and record it on the card

**Files:**

- Modify: `card/card.py` (imports; `FinalCard`; the sizing block at `card/card.py:294-347`; the veto-clear line at `card/card.py:351`; the `FinalCard(...)` return)
- Modify: `card/render.py` (the `size:` line)
- Test: `tests/test_card_card.py`, `tests/test_card_render.py`

**Interfaces:**

- Consumes: `resolve_capital` from Task 1.
- Produces: `FinalCard.capital_used: float | None` and
  `FinalCard.capital_source: str | None` (`"live_equity"` | `"config"` | `None`).
  These reach `docs/plans/ai-cards.jsonl` automatically — `append_ledgers` writes
  `final.to_dict()`, which is `asdict(self)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_card_card.py` inside the existing `TestPostPass` class. The file
already has `_trade_obj`, `_state_for_post` and `_post` helpers — reuse them. Note
`_state_for_post(account=...)` defaults to `None`, which is why the existing
`risk_usd == 25.0` tests keep passing unchanged.

```python
    def test_sizes_off_live_equity_when_present(self) -> None:
        account = AccountState(
            positions=[], daily_pnl_usd=0.0, daily_r=0.0, equity_usd=1201.33
        )
        final = _post(_trade_obj(), _state_for_post(account=account))
        assert final.verdict == "TRADE"
        # r_base 0.25% of 1201.33 = 3.0033 USD; |entry-sl| = 2 -> 1.50 units
        assert final.risk_usd == pytest.approx(3.003325)
        assert final.size_units == pytest.approx(1.5016625)
        assert final.capital_used == pytest.approx(1201.33)
        assert final.capital_source == "live_equity"

    def test_falls_back_to_config_capital_and_warns(self) -> None:
        final = _post(_trade_obj(), _state_for_post())
        assert final.risk_usd == 25.0
        assert final.capital_used == pytest.approx(10_000.0)
        assert final.capital_source == "config"
        assert any("configured capital" in w for w in final.warnings)

    def test_vetoed_card_clears_capital_fields(self) -> None:
        # min_rr veto: tp1 too close to entry for the 2.0 risk-per-unit.
        final = _post(_trade_obj(tp1=100.5), _state_for_post())
        assert final.verdict == "VETOED"
        assert final.capital_used is None
        assert final.capital_source is None
```

For `tests/test_card_render.py`, note two traps in the existing `_final` helper
(`tests/test_card_render.py:13`): its signature is `_final(verdict: str, **card_overrides)`,
so `verdict` is **positional** and every keyword is applied to the *card JSON*, not to
the `FinalCard`. Passing `capital_used=` to it would silently become a card field. Use
`dataclasses.replace` instead — that module is already imported at the top of the file:

```python
def test_render_states_the_capital_behind_the_risk() -> None:
    final = dataclasses.replace(
        _final("TRADE"), capital_used=1201.33, capital_source="live_equity"
    )
    out = render_card(final)
    assert "of 1,201.33 live_equity" in out


def test_render_omits_the_capital_note_when_absent() -> None:
    final = dataclasses.replace(_final("TRADE"), capital_used=None, capital_source=None)
    out = render_card(final)
    assert "% of capital" not in out
    assert "risk $25.00 (0.25%)" in out
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
poetry run pytest tests/test_card_card.py tests/test_card_render.py -q
```

Expected: FAIL — `FinalCard` has no attribute `capital_used`.

- [ ] **Step 3: Add the new fields to BOTH test FinalCard builders**

`FinalCard` is constructed directly in exactly three places — `card/card.py:352` and two
test helpers. Because the dataclass has no defaults, adding required fields breaks both
helpers with `TypeError: missing 2 required positional arguments` before any assertion
runs.

In `tests/test_card_render.py:30`, whose builder gates each sizing field on the verdict,
match that style — add beside the existing `risk_frac=` line:

```python
        capital_used=10_000.0 if verdict == "TRADE" else None,
        capital_source="config" if verdict == "TRADE" else None,
```

In `tests/test_card_ledger.py:30`, whose builder sets sizing fields unconditionally
(`size_units=12.5` with no verdict gate), match *that* style instead:

```python
        capital_used=10_000.0,
        capital_source="config",
```

No existing render assertion pins the `size:` line's wording — `tests/test_card_render.py:62`
asserts only `"risk $25.00" in out` — so changing the suffix breaks nothing.

- [ ] **Step 4: Write the implementation**

In `card/card.py`, add `resolve_capital` to the existing `from portfolio.sizing import`
block. Add two fields to `FinalCard`, directly after `risk_frac`:

```python
    risk_frac: float | None
    capital_used: float | None
    capital_source: str | None
```

Declare the locals beside the other sizing locals near `card/card.py:211`:

```python
    capital_used: float | None = None
    capital_source: str | None = None
```

In the sizing block, replace the two `sizing.capital` reads. The `else:` on
`r_adm <= 0.0` currently opens with `risk_frac = r_adm`:

```python
            else:
                equity = state.account.equity_usd if state.account is not None else None
                capital, used_live = resolve_capital(sizing, equity)
                capital_used = capital
                capital_source = "live_equity" if used_live else "config"
                if not used_live:
                    warnings.append(
                        f"sized off configured capital ${capital:,.2f} — live "
                        "equity unavailable, so the risk fraction is against a "
                        "constant, not the account"
                    )
                risk_frac = r_adm
                risk_usd = capital * r_adm
                size_units = position_size(risk_usd, entry, sl)
```

and inside the `qty_step` rounding branch, replace the restatement divisor:

```python
                        risk_usd = size_units * risk_per_unit(entry, sl)
                        risk_frac = risk_usd / capital if capital > 0 else r_adm
```

Extend the veto-clear line at `card/card.py:351`:

```python
    if veto:
        size_units = notional_usd = risk_usd = risk_frac = rr_tp1 = None
        capital_used = capital_source = None
```

and pass both through in the `FinalCard(...)` return, after `risk_frac=risk_frac,`:

```python
        capital_used=capital_used,
        capital_source=capital_source,
```

In `card/render.py`, change the `size:` line so the percentage is never quoted without
the capital it is a percentage of:

```python
        capital_note = (
            f" of {final.capital_used:,.2f} {final.capital_source}"
            if final.capital_used is not None and final.capital_source is not None
            else ""
        )
        lines.append(
            f"size: {final.size_units} units · notional "
            f"${final.notional_usd:.2f} · risk ${final.risk_usd:.2f} "
            f"({(final.risk_frac or 0.0) * 100:.2f}%{capital_note})"
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
poetry run pytest tests/test_card_card.py tests/test_card_render.py tests/test_card_ledger.py -q
```

Expected: PASS. `test_card_ledger.py` is included because `append_ledgers` serialises
`asdict(final)`; if it asserts an exact dict, add the two new keys to its expectation.

- [ ] **Step 6: Commit**

```bash
git add card/card.py card/render.py tests/test_card_card.py tests/test_card_render.py tests/test_card_ledger.py
git commit -m "fix(card): size off real equity and record the capital behind each card"
```

---

### Task 4: Docs sync and the full Definition of Done

**Files:**

- Modify: `CLAUDE.md` (the `buibui card` bullet in the CLI section)
- Modify: `.claude/context/signals.md` (the card entry)
- Modify: `.claude/skills/card/SKILL.md`
- Check only: `README.md`

- [ ] **Step 1: Find every doc surface that states the old behaviour**

```bash
grep -rn '10,000\|10_000\|0.25% of capital\|hardcoded' CLAUDE.md README.md .claude/context/ .claude/skills/card/
```

Read each hit and decide whether it describes card sizing. Do not blind-replace — the
same numbers appear in unrelated overlay and portfolio text.

- [ ] **Step 2: Update the `buibui card` bullet in `CLAUDE.md`**

Add to that bullet, after the existing sizing sentence:

```markdown
Sizing resolves capital via `portfolio.sizing.resolve_capital` — **live account
equity when available, the configured `[portfolio] capital` otherwise** — and every
card records `capital_used` / `capital_source`, because once capital is live a bare
`risk_frac` is uninterpretable after the fact. A pinned `--as-of` run omits the
account by design and therefore always takes the config path, with a warning. The
same resolved capital scales `daily_r`, so the daily circuit breaker is measured in
real R: against the old `10_000.0` constant its R unit was `$25` while the account's
was `$3`, and a true −2R day passed the gate as −0.24R.
```

- [ ] **Step 3: Update `.claude/context/signals.md` and the card SKILL.md**

Mirror the same two facts in each — capital comes from live equity with a config
fallback, and `capital_used` / `capital_source` are on every card and in the ledger.
Keep each to one or two sentences; the deep version lives in `CLAUDE.md`.

- [ ] **Step 4: Run the full Definition of Done**

```bash
make lint-py
make typecheck
make test
make test-regression
```

Expected: all four green, and `make test-regression` **unmoved** — the card is not in
the backtest pipeline, so a golden move here means something unintended was touched.
Investigate rather than regenerating. State each result plainly, including any that
was skipped or failed.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md .claude/context/signals.md .claude/skills/card/SKILL.md
git commit -m "docs: card sizing now resolves capital from live equity"
```

---

## Post-implementation

Run `/post-branch` **before** `gh pr create` — Steps 1-5 and 7 are commit-producing and
must ship in the initial push; Steps 6 and 10a/10c run after the PR exists. Pushing a
doc fix onto an open PR re-runs the whole CI suite for one paragraph.

**Manual check worth doing once, since no test covers it:** run one real
`make buibui-card SYMBOL=BTCUSDT` against the live account and confirm the rendered
`size:` line names a capital close to the account's actual equity rather than
`10,000.00`. Avoid starting it on `:01/16/31/46` — the signal-watch timer holds
`analytics.db` and DuckDB refuses even a read-only open, which kills the card with an
`IOException`.
