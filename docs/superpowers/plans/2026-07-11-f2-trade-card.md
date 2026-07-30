# F2 AI Trade Card v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command — `buibui card BTCUSDT` — produces a structured,
evidence-cited trade card (TRADE / NO_TRADE / VETOED) from the system's
shipped state surfaces, with position size computed deterministically in code
and every card persisted as a scoreable forecast.

**Architecture:** New top-level `card/` package (peer of `portfolio/` and
`trade/`): a pure state serialiser composes shipped surfaces (brief panel
incl. M1 indicators + M2 sessions, pundit board, XS target book, recent
detector fires, injected live-account provider) into a `MarketState`; a
versioned prompt + `claude-personal -p` subprocess client produce a JSON
card; a deterministic post-pass computes size and enforces hard rules; a
dual-ledger writer makes every TRADE card scoreable by the existing pundit
scorer with zero scorer changes. Spec:
`docs/superpowers/specs/2026-07-08-f2-trade-card-design.md` (**read the
2026-07-11 Addendum — it overrides the body where they conflict**).

**Tech Stack:** Python 3.11+, dataclasses, DuckDB (read-only), pandas,
subprocess (injectable), pytest + fakes (no network, no subprocess in tests).

## Global Constraints

- mypy strict: every function fully annotated (`-> None` on test methods);
  `from __future__ import annotations` at the top of every new module.
- ruff format + lint must pass (`make lint-py`).
- Tests: `duckdb.connect(":memory:")` only; **no network, no subprocess** —
  `runner`, `AccountProvider`, `brief_fn`, `targets_fn` are injected fakes.
- Regression goldens must not move (`make test-regression` — no
  detector/backtest/stats change anywhere in this plan).
- `docs/plans/` is gitignored — both ledger files live there; never commit
  ledger contents.
- The LLM subprocess env guard is **load-bearing**: strip
  `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`, set
  `CLAUDE_CONFIG_DIR=~/.claude-personal` (expanded), cwd = fresh temp dir.
  Never point at the work Claude account (bare default config).
- The LLM never computes size/risk — `post_pass` decides money (spec D6).
- Commit after every task (conventional commits, `feat:`/`test:` prefixes).
- Run `make lint-py && make typecheck` before every commit; targeted pytest
  per task; the full `make test` + `make test-regression` sweep is Task 12.

---

### Task 1: `card/` package — errors + `CardConfig`

**Files:**

- Create: `card/__init__.py`
- Create: `card/errors.py`
- Create: `card/config.py`
- Test: `tests/test_card_config.py`

**Interfaces:**

- Consumes: nothing (first task).
- Produces: `CardError(Exception)`, `CardValidationError(CardError)` with
  `.errors: list[str]`; `CardConfig` frozen dataclass (fields below) with
  `CardConfig.from_toml(path: str | Path) -> CardConfig`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_card_config.py
"""CardConfig defaults + from_toml ([card] block)."""

from __future__ import annotations

from pathlib import Path

import pytest

from card.config import CardConfig
from card.errors import CardError, CardValidationError


class TestCardConfig:
    def test_defaults(self) -> None:
        cfg = CardConfig()
        assert cfg.claude_bin == "claude"
        assert cfg.claude_config_dir == "~/.claude-personal"
        assert cfg.model == "sonnet"
        assert cfg.timeout_s == 180.0
        assert cfg.min_rr == 1.0
        assert cfg.daily_loss_limit_r == -2.0
        assert cfg.valid_hours == 12.0
        assert cfg.entry_band_pct == 5.0
        assert cfg.fires_lookback_bars == 4
        assert cfg.fires_timeframes == ("1h", "4h", "1d")
        assert cfg.ratings_config == "signal_watch"
        assert cfg.sizing_toml is None
        assert cfg.cards_path == "docs/plans/ai-cards.jsonl"
        assert cfg.pundit_calls_path == "docs/plans/pundit-calls.jsonl"
        assert cfg.priors_path == "docs/plans/pundit-priors.json"
        assert cfg.targets_dir == "docs/plans/xsmom_targets"

    def test_from_toml_overrides(self, tmp_path: Path) -> None:
        toml = tmp_path / "card.toml"
        toml.write_text(
            '[card]\nmodel = "haiku"\nmin_rr = 1.5\nfires_timeframes = ["4h", "1d"]\n'
        )
        cfg = CardConfig.from_toml(toml)
        assert cfg.model == "haiku"
        assert cfg.min_rr == 1.5
        assert cfg.fires_timeframes == ("4h", "1d")
        assert cfg.claude_bin == "claude"  # untouched default

    def test_from_toml_missing_block_is_defaults(self, tmp_path: Path) -> None:
        toml = tmp_path / "empty.toml"
        toml.write_text("[other]\nx = 1\n")
        assert CardConfig.from_toml(toml) == CardConfig()

    def test_from_toml_unknown_key_raises(self, tmp_path: Path) -> None:
        toml = tmp_path / "bad.toml"
        toml.write_text("[card]\nnot_a_field = 1\n")
        with pytest.raises(ValueError, match="unknown"):
            CardConfig.from_toml(toml)

    def test_error_hierarchy(self) -> None:
        err = CardValidationError(["a", "b"])
        assert isinstance(err, CardError)
        assert err.errors == ["a", "b"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_card_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'card'`

- [ ] **Step 3: Write the implementation**

```python
# card/__init__.py
"""AI trade-card package (F2): advisory cards, no order routing."""
```

```python
# card/errors.py
"""Card-generation error types."""

from __future__ import annotations


class CardError(Exception):
    """Unrecoverable card-generation failure (nothing is written)."""


class CardValidationError(CardError):
    """LLM output failed schema validation; carries the error list."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors
```

```python
# card/config.py
"""CardConfig — frozen config for `buibui card` (optional [card] TOML block)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CardConfig:
    claude_bin: str = "claude"
    claude_config_dir: str = "~/.claude-personal"
    model: str = "sonnet"
    timeout_s: float = 180.0
    min_rr: float = 1.0
    daily_loss_limit_r: float = -2.0
    valid_hours: float = 12.0
    entry_band_pct: float = 5.0
    fires_lookback_bars: int = 4
    fires_timeframes: tuple[str, ...] = ("1h", "4h", "1d")
    ratings_config: str = "signal_watch"
    sizing_toml: str | None = None
    cards_path: str = "docs/plans/ai-cards.jsonl"
    pundit_calls_path: str = "docs/plans/pundit-calls.jsonl"
    priors_path: str = "docs/plans/pundit-priors.json"
    targets_dir: str = "docs/plans/xsmom_targets"

    @classmethod
    def from_toml(cls, path: str | Path) -> CardConfig:
        """Build from a TOML file's optional `[card]` table.

        Missing block/keys keep dataclass defaults (SizingConfig pattern);
        unknown keys raise so typos never silently no-op.
        """
        with open(Path(path), "rb") as f:
            data: dict[str, Any] = tomllib.load(f)
        block = data.get("card", {})
        if not isinstance(block, dict):
            raise ValueError("[card] must be a TOML table")
        known = {f.name for f in fields(cls)}
        unknown = set(block) - known
        if unknown:
            raise ValueError(f"[card] unknown keys: {sorted(unknown)}")
        kwargs: dict[str, Any] = dict(block)
        if "fires_timeframes" in kwargs:
            kwargs["fires_timeframes"] = tuple(kwargs["fires_timeframes"])
        return cls(**kwargs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_card_config.py -v`
Expected: 5 passed

- [ ] **Step 5: Gates + commit**

```bash
make lint-py && make typecheck
git add card/ tests/test_card_config.py
git commit -m "feat(card): F2 package skeleton — errors + CardConfig"
```

---

### Task 2: `card/state.py` — dataclasses, provider protocol, digest

**Files:**

- Create: `card/state.py`
- Test: `tests/test_card_state.py`

**Interfaces:**

- Consumes: `analytics.brief.types.SymbolPanel / SessionClock / PunditBoard`
  (shipped dataclasses), `card.config.CardConfig`.
- Produces (later tasks rely on these exact names):
  `OpenPosition(symbol, side, qty, entry, mark, upnl_usd)`;
  `AccountState(positions, daily_pnl_usd, daily_r, equity_usd)`;
  `AccountProvider` Protocol with `positions() -> list[OpenPosition]`,
  `daily_pnl_usd(start_ms: int, end_ms: int) -> float`,
  `equity_usd() -> float | None`;
  `RecentFire(strategy, tf, direction, open_time, entry_price, stars,
  avg_r, win_rate, dsr)`;
  `MarketState(symbol, now_ms, direction_hint, panel, session_clock,
  pundit, xs, recent_fires, account, health)` with
  `to_dict() -> dict[str, Any]`;
  `state_digest(state: MarketState) -> str` (sha256 hex).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_card_state.py
"""MarketState dataclasses, to_dict determinism, digest."""

from __future__ import annotations

from card.state import (
    AccountState,
    MarketState,
    OpenPosition,
    RecentFire,
    state_digest,
)


def _minimal_state(**overrides: object) -> MarketState:
    base: dict[str, object] = {
        "symbol": "BTCUSDT",
        "now_ms": 1_760_000_000_000,
        "direction_hint": None,
        "panel": None,
        "session_clock": None,
        "pundit": None,
        "xs": None,
        "recent_fires": [],
        "account": None,
        "health": [],
    }
    base.update(overrides)
    return MarketState(**base)  # type: ignore[arg-type]


class TestMarketState:
    def test_to_dict_is_json_safe_and_ordered(self) -> None:
        state = _minimal_state(
            recent_fires=[
                RecentFire(
                    strategy="fvg",
                    tf="4h",
                    direction="long",
                    open_time=1,
                    entry_price=100.0,
                    stars=3,
                    avg_r=0.12,
                    win_rate=0.5,
                    dsr=0.9,
                )
            ],
            account=AccountState(
                positions=[
                    OpenPosition(
                        symbol="BTCUSDT",
                        side="long",
                        qty=0.1,
                        entry=100.0,
                        mark=101.0,
                        upnl_usd=0.1,
                    )
                ],
                daily_pnl_usd=-5.0,
                daily_r=-0.2,
                equity_usd=1000.0,
            ),
        )
        d = state.to_dict()
        assert d["symbol"] == "BTCUSDT"
        assert d["recent_fires"][0]["strategy"] == "fvg"
        assert d["account"]["positions"][0]["side"] == "long"

    def test_digest_deterministic_and_input_sensitive(self) -> None:
        a = _minimal_state()
        b = _minimal_state()
        c = _minimal_state(symbol="ETHUSDT")
        assert state_digest(a) == state_digest(b)
        assert state_digest(a) != state_digest(c)
        assert len(state_digest(a)) == 64
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_card_state.py -v`
Expected: FAIL with import error on `card.state`

- [ ] **Step 3: Write the implementation (dataclasses half of `card/state.py`)**

```python
# card/state.py
"""G12 market-state serialiser: snapshot_market_state -> MarketState."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import duckdb
import pandas as pd

from analytics.brief.types import PunditBoard, SessionClock, SymbolPanel

DAY_MS = 86_400_000


@dataclass(frozen=True)
class OpenPosition:
    symbol: str
    side: str  # "long" | "short"
    qty: float
    entry: float
    mark: float
    upnl_usd: float


@dataclass(frozen=True)
class AccountState:
    positions: list[OpenPosition]
    daily_pnl_usd: float
    daily_r: float  # daily_pnl_usd / (capital * r_base)
    equity_usd: float | None


class AccountProvider(Protocol):
    """Read-only live-account seam; the CLI injects the real one."""

    def positions(self) -> list[OpenPosition]: ...

    def daily_pnl_usd(self, start_ms: int, end_ms: int) -> float: ...

    def equity_usd(self) -> float | None: ...


@dataclass(frozen=True)
class RecentFire:
    strategy: str
    tf: str
    direction: str
    open_time: int
    entry_price: float
    stars: int | None
    avg_r: float | None
    win_rate: float | None
    dsr: float | None


@dataclass(frozen=True)
class MarketState:
    symbol: str
    now_ms: int
    direction_hint: str | None
    panel: SymbolPanel | None
    session_clock: SessionClock | None
    pundit: PunditBoard | None
    xs: dict[str, Any] | None
    recent_fires: list[RecentFire]
    account: AccountState | None
    health: list[str]

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict (nested dataclasses flattened by asdict)."""
        return asdict(self)


def state_digest(state: MarketState) -> str:
    """sha256 of the canonical (sorted-keys) state JSON — the audit anchor."""
    return hashlib.sha256(
        json.dumps(state.to_dict(), sort_keys=True).encode()
    ).hexdigest()
```

(The `duckdb`, `pandas`, `Path` imports are used by Task 3's builders — ruff
will flag them as unused right now; either add them in Task 3 or add them now
with `# noqa: F401` removed in Task 3. Prefer: add only
`hashlib/json/asdict/dataclass/Any/Protocol` + brief types now, the rest in
Task 3.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_card_state.py -v`
Expected: 2 passed

- [ ] **Step 5: Gates + commit**

```bash
make lint-py && make typecheck
git add card/state.py tests/test_card_state.py
git commit -m "feat(card): MarketState dataclasses + AccountProvider protocol + digest"
```

---

### Task 3: state block builders + `snapshot_market_state` + ratings getter

**Files:**

- Modify: `analytics/store/confidence.py` (append one getter)
- Modify: `card/state.py` (append builders + snapshot)
- Test: `tests/test_card_state.py` (extend)

**Interfaces:**

- Consumes: `analytics.brief.bundle.compute_brief(conn, BriefConfig,
  extra_notes=None) -> BriefBundle`; `analytics.brief.config.BriefConfig
  (symbols, as_of_ms, ledger_path, priors_path, ...)`;
  `analytics.store.signals.get_signals_history(conn, symbol, timeframe,
  start_ms, end_ms) -> pd.DataFrame` (columns: symbol, timeframe, strategy,
  open_time, direction, entry_price, sl_price, reason, confidence,
  fired_at); `analytics.signal._common.parse_timeframe_secs(tf) -> int`;
  `analytics.xsmom.replay.replay_targets(conn, cfg, capital, symbols=None,
  *, now=None) -> TargetBook`; `analytics.xsmom.live.target_book_to_dict
  (book) -> dict`; `analytics.forecast.config.ForecastConfig()`;
  `portfolio.sizing.SizingConfig`.
- Produces: `get_confidence_rating_rows(conn, config_name) ->
  dict[tuple[str, str, str], dict[str, float | int | None]]` keyed
  `(strategy, tf, direction)` with keys `stars/avg_r/win_rate/dsr`;
  `snapshot_market_state(conn, symbol, cfg, sizing, *, now_ms,
  account_provider, direction_hint=None, brief_fn=compute_brief,
  targets_fn=replay_targets) -> MarketState`.

- [ ] **Step 1: Write the failing tests (append to `tests/test_card_state.py`)**

```python
# append to tests/test_card_state.py
import duckdb

from analytics.brief.types import (
    BriefBundle,
    HealthReport,
    PunditBoard,
    error_panel,
)
from analytics.store.confidence import get_confidence_rating_rows
from analytics.store.schema import init_schema
from card.config import CardConfig
from card.state import snapshot_market_state
from portfolio.sizing import SizingConfig

_NOW_MS = 1_760_000_000_000


def _fake_bundle(symbol: str) -> BriefBundle:
    return BriefBundle(
        as_of_ms=_NOW_MS,
        day_ahead="Fri 2026-07-10",
        session_clock=None,
        panels=[error_panel(symbol, "no data")],
        pundit=PunditBoard(
            priors_status="absent",
            priors_age_days=None,
            min_n_marker=None,
            ledger_status="absent",
            ledger_total=0,
            ledger_skipped=0,
            recent_calls=[],
            authors=[],
            families=[],
        ),
        health=HealthReport(rows=[], notes=[], data_ok=False),
    )


class FakeProvider:
    def positions(self) -> list:  # type: ignore[type-arg]
        from card.state import OpenPosition

        return [
            OpenPosition(
                symbol="BTCUSDT",
                side="short",
                qty=0.5,
                entry=100.0,
                mark=99.0,
                upnl_usd=0.5,
            )
        ]

    def daily_pnl_usd(self, start_ms: int, end_ms: int) -> float:
        return -50.0

    def equity_usd(self) -> float | None:
        return 9_000.0


class TestConfidenceRatingRows:
    def test_rows_keyed_by_strategy_tf_direction(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        conn.execute(
            "INSERT INTO confidence_ratings "
            "(config_name, strategy, tf, direction, stars, avg_r, win_rate, "
            "updated_at_ms, day_filter, dsr) "
            "VALUES ('signal_watch', 'fvg', '4h', 'combined', 3, 0.12, 0.5, "
            "0, NULL, 0.9)"
        )
        rows = get_confidence_rating_rows(conn, "signal_watch")
        assert rows[("fvg", "4h", "combined")]["stars"] == 3
        assert rows[("fvg", "4h", "combined")]["avg_r"] == 0.12
        assert get_confidence_rating_rows(conn, "other") == {}


class TestSnapshotMarketState:
    def test_composes_blocks_with_injected_seams(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        conn.execute(
            "INSERT INTO signals (symbol, timeframe, strategy, open_time, "
            "direction, entry_price, sl_price, reason, confidence, fired_at) "
            f"VALUES ('BTCUSDT', '1h', 'fvg', {_NOW_MS - 1_000_000}, 'long', "
            "100.0, 99.0, 'r', 3, 0)"
        )
        conn.execute(
            "INSERT INTO confidence_ratings "
            "(config_name, strategy, tf, direction, stars, avg_r, win_rate, "
            "updated_at_ms, day_filter, dsr) "
            "VALUES ('signal_watch', 'fvg', '1h', 'long', 4, 0.2, 0.6, 0, "
            "NULL, 0.96)"
        )
        state = snapshot_market_state(
            conn,
            "BTCUSDT",
            CardConfig(),
            SizingConfig(),
            now_ms=_NOW_MS,
            account_provider=FakeProvider(),
            direction_hint="long",
            brief_fn=lambda _conn, cfg: _fake_bundle(cfg.symbols[0]),
            targets_fn=lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("no 1d data")
            ),
        )
        assert state.symbol == "BTCUSDT"
        assert state.direction_hint == "long"
        # panel: error panel surfaced + health note
        assert state.panel is not None and state.panel.error == "no data"
        assert any("panel" in n for n in state.health)
        # pundit: the brief's PunditBoard, composed not re-parsed
        assert state.pundit is not None
        assert state.pundit.priors_status == "absent"
        # xs: builder raised -> None + health note (no snapshot file, targets_fn raises)
        assert state.xs is None
        assert any(n.startswith("xs:") for n in state.health)
        # fires: annotated from confidence_ratings (direction-specific row)
        assert len(state.recent_fires) == 1
        assert state.recent_fires[0].stars == 4
        assert state.recent_fires[0].dsr == 0.96
        # account: daily_r = -50 / (10_000 * 0.0025) = -2.0
        assert state.account is not None
        assert state.account.daily_r == -2.0
        assert state.account.positions[0].side == "short"

    def test_no_provider_degrades_with_note(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        state = snapshot_market_state(
            conn,
            "BTCUSDT",
            CardConfig(),
            SizingConfig(),
            now_ms=_NOW_MS,
            account_provider=None,
            brief_fn=lambda _conn, cfg: _fake_bundle(cfg.symbols[0]),
            targets_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")),
        )
        assert state.account is None
        assert any(n.startswith("account:") for n in state.health)

    def test_as_of_determinism(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)

        def _snap() -> str:
            from card.state import state_digest

            return state_digest(
                snapshot_market_state(
                    conn,
                    "BTCUSDT",
                    CardConfig(),
                    SizingConfig(),
                    now_ms=_NOW_MS,
                    account_provider=None,
                    brief_fn=lambda _conn, cfg: _fake_bundle(cfg.symbols[0]),
                    targets_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")),
                )
            )

        assert _snap() == _snap()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_card_state.py -v`
Expected: FAIL — `get_confidence_rating_rows` / `snapshot_market_state`
not defined

- [ ] **Step 3: Implement the store getter (append to `analytics/store/confidence.py`)**

```python
def get_confidence_rating_rows(
    conn: duckdb.DuckDBPyConnection,
    config_name: str,
) -> dict[tuple[str, str, str], dict[str, float | int | None]]:
    """Full rating rows for one config, keyed (strategy, tf, direction).

    Values carry stars / avg_r / win_rate / dsr (all the quality columns the
    table persists — there is no n column). Empty dict when unwritten.
    """
    rows = conn.execute(
        "SELECT strategy, tf, direction, stars, avg_r, win_rate, dsr "
        "FROM confidence_ratings WHERE config_name = ?",
        [config_name],
    ).fetchall()
    return {
        (str(r[0]), str(r[1]), str(r[2])): {
            "stars": r[3],
            "avg_r": r[4],
            "win_rate": r[5],
            "dsr": r[6],
        }
        for r in rows
    }
```

Also re-export it from the shim `analytics/data_store.py` alongside the
existing confidence re-exports (match the file's existing import list
style — one added name in the `from analytics.store.confidence import ...`
line and in `__all__` if the shim defines one).

- [ ] **Step 4: Implement builders + snapshot (append to `card/state.py`)**

Add these imports at the top of `card/state.py` (removing any Task 2
leftovers ruff flags):

```python
from collections.abc import Callable

import duckdb
import pandas as pd

from analytics.brief.bundle import compute_brief
from analytics.brief.config import BriefConfig
from analytics.forecast.config import ForecastConfig
from analytics.signal._common import parse_timeframe_secs
from analytics.store.confidence import get_confidence_rating_rows
from analytics.store.signals import get_signals_history
from analytics.xsmom.live import target_book_to_dict
from analytics.xsmom.replay import replay_targets
from card.config import CardConfig
from portfolio.sizing import SizingConfig
```

Then append:

```python
BriefFn = Callable[..., Any]
TargetsFn = Callable[..., Any]


def _xs_block(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    capital: float,
    now_ms: int,
    targets_dir: str,
    targets_fn: TargetsFn,
) -> dict[str, Any] | None:
    """Symbol's XS target row + book governor; None when not in the book.

    Prefers today's gitignored snapshot (docs/plans/xsmom_targets/<date>.json)
    so the card matches what the executor saw; falls back to a fresh
    replay_targets computation.
    """
    now = pd.Timestamp(now_ms, unit="ms", tz="UTC")
    snap = Path(targets_dir) / f"{now.date().isoformat()}.json"
    if snap.exists():
        book: dict[str, Any] = json.loads(snap.read_text(encoding="utf-8"))
    else:
        book = target_book_to_dict(targets_fn(conn, ForecastConfig(), capital, now=now))
    row = next(
        (
            p
            for p in book.get("positions", [])
            if p.get("symbol") == symbol and p.get("side") != "flat"
        ),
        None,
    )
    if row is None:
        return None
    return {
        **row,
        "governor": book.get("governor"),
        "as_of_date": book.get("as_of_date"),
    }


def _fires_block(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    cfg: CardConfig,
    now_ms: int,
) -> list[RecentFire]:
    """Fired events in the last fires_lookback_bars per TF, quality-annotated."""
    ratings = get_confidence_rating_rows(conn, cfg.ratings_config)
    fires: list[RecentFire] = []
    for tf in cfg.fires_timeframes:
        span_ms = parse_timeframe_secs(tf) * 1000 * cfg.fires_lookback_bars
        df = get_signals_history(conn, symbol, tf, now_ms - span_ms, now_ms)
        for row in df.itertuples(index=False):
            r = ratings.get((str(row.strategy), tf, str(row.direction)))
            if r is None:
                r = ratings.get((str(row.strategy), tf, "combined"))
            fires.append(
                RecentFire(
                    strategy=str(row.strategy),
                    tf=tf,
                    direction=str(row.direction),
                    open_time=int(row.open_time),
                    entry_price=float(row.entry_price),
                    stars=int(r["stars"]) if r and r["stars"] is not None else None,
                    avg_r=float(r["avg_r"]) if r and r["avg_r"] is not None else None,
                    win_rate=(
                        float(r["win_rate"])
                        if r and r["win_rate"] is not None
                        else None
                    ),
                    dsr=float(r["dsr"]) if r and r["dsr"] is not None else None,
                )
            )
    return fires


def snapshot_market_state(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    cfg: CardConfig,
    sizing: SizingConfig,
    *,
    now_ms: int,
    account_provider: AccountProvider | None,
    direction_hint: str | None = None,
    brief_fn: BriefFn = compute_brief,
    targets_fn: TargetsFn = replay_targets,
) -> MarketState:
    """Compose the full market state; one failing block never kills the card."""
    health: list[str] = []

    panel: SymbolPanel | None = None
    clock: SessionClock | None = None
    pundit: PunditBoard | None = None
    try:
        bundle = brief_fn(
            conn,
            BriefConfig(
                symbols=(symbol,),
                as_of_ms=now_ms,
                ledger_path=Path(cfg.pundit_calls_path),
                priors_path=Path(cfg.priors_path),
            ),
        )
        panel = bundle.panels[0] if bundle.panels else None
        clock = bundle.session_clock
        pundit = bundle.pundit
        if panel is not None and panel.error is not None:
            health.append(f"panel: {panel.error}")
    except Exception as exc:
        health.append(f"panel: {exc}")

    xs: dict[str, Any] | None = None
    try:
        xs = _xs_block(
            conn, symbol, sizing.capital, now_ms, cfg.targets_dir, targets_fn
        )
    except Exception as exc:
        health.append(f"xs: {exc}")

    fires: list[RecentFire] = []
    try:
        fires = _fires_block(conn, symbol, cfg, now_ms)
    except Exception as exc:
        health.append(f"recent_fires: {exc}")

    account: AccountState | None = None
    if account_provider is None:
        health.append("account: no provider (degraded)")
    else:
        try:
            day_start = now_ms - (now_ms % DAY_MS)
            pnl = account_provider.daily_pnl_usd(day_start, now_ms)
            account = AccountState(
                positions=account_provider.positions(),
                daily_pnl_usd=pnl,
                daily_r=pnl / (sizing.capital * sizing.r_base),
                equity_usd=account_provider.equity_usd(),
            )
        except Exception as exc:
            health.append(f"account: {exc}")

    return MarketState(
        symbol=symbol,
        now_ms=now_ms,
        direction_hint=direction_hint,
        panel=panel,
        session_clock=clock,
        pundit=pundit,
        xs=xs,
        recent_fires=fires,
        account=account,
        health=health,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/test_card_state.py -v`
Expected: all pass (Task 2's + the 4 new)

- [ ] **Step 6: Gates + commit**

```bash
make lint-py && make typecheck
git add card/state.py analytics/store/confidence.py analytics/data_store.py tests/test_card_state.py
git commit -m "feat(card): snapshot_market_state block composition + ratings-rows getter"
```

---

### Task 4: `card/prompt.py` — versioned rubric + payload builder

**Files:**

- Create: `card/prompt.py`
- Test: `tests/test_card_prompt.py`

**Interfaces:**

- Consumes: `card.state.MarketState`, `card.config.CardConfig`.
- Produces: `PROMPT_VERSION: str` (`"card-v1"`), `RUBRIC: str`,
  `build_prompt(state: MarketState, cfg: CardConfig) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_card_prompt.py
"""Prompt: byte-stable rubric, state embedding, direction hint."""

from __future__ import annotations

import json

from card.config import CardConfig
from card.prompt import PROMPT_VERSION, RUBRIC, build_prompt
from card.state import MarketState


def _state(hint: str | None = None) -> MarketState:
    return MarketState(
        symbol="BTCUSDT",
        now_ms=1_760_000_000_000,
        direction_hint=hint,
        panel=None,
        session_clock=None,
        pundit=None,
        xs=None,
        recent_fires=[],
        account=None,
        health=["panel: no data"],
    )


class TestPrompt:
    def test_version_constant(self) -> None:
        assert PROMPT_VERSION == "card-v1"

    def test_rubric_prefix_is_byte_stable(self) -> None:
        cfg = CardConfig()
        p1 = build_prompt(_state(), cfg)
        p2 = build_prompt(_state(), cfg)
        assert p1 == p2
        assert p1.startswith(RUBRIC)

    def test_state_json_embedded_sorted(self) -> None:
        state = _state()
        prompt = build_prompt(state, CardConfig())
        assert json.dumps(state.to_dict(), sort_keys=True) in prompt

    def test_direction_hint_included_only_when_set(self) -> None:
        cfg = CardConfig()
        assert "operator is considering" not in build_prompt(_state(), cfg)
        hinted = build_prompt(_state("long"), cfg)
        assert "operator is considering a long" in hinted

    def test_rubric_names_no_absent_indicators(self) -> None:
        # fields that don't exist must never be claimed (spec + addendum)
        assert "RSI" not in RUBRIC
        assert "MACD" not in RUBRIC

    def test_schema_and_hard_rules_inlined(self) -> None:
        assert '"verdict"' in RUBRIC
        assert "ONLY a JSON object" in RUBRIC
        assert "confluence" in RUBRIC.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_card_prompt.py -v`
Expected: FAIL with import error on `card.prompt`

- [ ] **Step 3: Write the implementation**

```python
# card/prompt.py
"""Versioned card prompt: static rubric + deterministic state payload."""

from __future__ import annotations

import json

from card.config import CardConfig
from card.state import MarketState

PROMPT_VERSION = "card-v1"

_SCHEMA = """{
  "verdict": "TRADE" or "NO_TRADE",
  "direction": "long" | "short" | null,
  "entry": number | null,
  "sl": number | null,
  "tp1": number | null,
  "tp2": number | null,
  "tp3": number | null,
  "confluence_score": integer 0-9,
  "reasoning": ["5 to 8 bullets, each citing a concrete number from the input"],
  "invalidation": "what price/structure event kills the idea",
  "expected_hold": "e.g. 6h, 2d",
  "valid_until_utc": "ISO-8601 timestamp",
  "no_trade_reason": "named failed gate" | null
}"""

RUBRIC = f"""You are a disciplined crypto-futures analyst producing ONE \
trade card for the symbol in the MARKET STATE JSON below. Work ONLY from \
numbers present in that JSON — never invent values, never cite indicators \
that are not in the input.

Method (in order):
1. Multi-timeframe synthesis: read panel.regime_1d / panel.regime_4h, the \
indicator block (EMA stack + slope, range/run-length state, Monday-range \
state, PA character, Bollinger %B / bandwidth / squeeze, anchored-VWAP \
distances, volume-profile POC/VAH/VAL and vs_value) and the session block \
(clock, last-3-session recap, tendencies). State the directional bias each \
timeframe supports.
2. Liquidity map: list the 3 nearest levels/zones ABOVE and BELOW from \
panel.levels_above/below and panel.zones_above/below with their dist_atr, \
timeframe, and swept flag. Prefer unswept levels as targets, swept-and- \
reclaimed as entries.
3. Confluence scan: score 0-9 how many independent inputs agree — zone/level \
geometry, indicator states, session tendency, recent_fires (weight by stars/\
avg_r/dsr; treat missing ratings or dsr < 0.95 as weak evidence), pundit \
priors (only authors/families with flagged=false), and the xs block (side + \
forecast = the system's own book lean).
4. Decision: TRADE only when a limit entry at a structural level, a \
structural SL beyond it, and TP1/TP2/TP3 at mapped liquidity give planned \
RR(tp1) >= 1. Otherwise NO_TRADE naming the failed gate in no_trade_reason.
5. Reasoning log: 5-8 bullets, each citing a concrete number from the input \
JSON. No hedge words (might/could/perhaps).

Hard rules (also enforced in code after you answer — violations are vetoed):
- Never propose a trade against an existing open position on this symbol \
(account.positions).
- If account.daily_r <= the circuit-breaker limit, answer NO_TRADE \
("circuit breaker").
- Entry must be within a few percent of panel.ref_close (no far-from-market \
limits).
- SL on the correct side of entry; TPs ordered away from entry.
- You never compute position size or risk USD — code does that.

Respond with ONLY a JSON object matching this schema (no prose before or \
after, no markdown fences):
{_SCHEMA}"""


def build_prompt(state: MarketState, cfg: CardConfig) -> str:
    """Rubric (byte-stable) + optional operator hint + sorted state JSON."""
    parts = [RUBRIC]
    if state.direction_hint:
        parts.append(
            f"The operator is considering a {state.direction_hint}; evaluate "
            "that side explicitly, and flag if the opposite side scores "
            "higher. You may still answer NO_TRADE."
        )
    parts.append("MARKET STATE JSON:\n" + json.dumps(state.to_dict(), sort_keys=True))
    return "\n\n".join(parts)
```

Note: `cfg` is accepted for future knobs (valid_hours in copy, etc.) — mypy
strict allows unused params; do NOT interpolate cfg values into `RUBRIC`
(it must stay a module constant, byte-stable across cards).

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_card_prompt.py -v`
Expected: 6 passed

- [ ] **Step 5: Gates + commit**

```bash
make lint-py && make typecheck
git add card/prompt.py tests/test_card_prompt.py
git commit -m "feat(card): card-v1 rubric + deterministic prompt builder"
```

---

### Task 5: `card/client.py` — the LLM seam

**Files:**

- Create: `card/client.py`
- Test: `tests/test_card_client.py`

**Interfaces:**

- Consumes: `card.errors.CardError`.
- Produces: `LLMResponse(text, model, cost_usd_notional, input_tokens,
  output_tokens)`; `LLMClient` Protocol with
  `generate(prompt: str) -> LLMResponse`; `ClaudeCliClient(binary, model,
  timeout_s, config_dir, runner=None)` frozen dataclass;
  `RunnerFn = Callable[..., subprocess.CompletedProcess[str]]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_card_client.py
"""ClaudeCliClient: command/env/cwd contract, envelope parsing, retry."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from card.client import ClaudeCliClient, LLMResponse
from card.errors import CardError


def _envelope(result: str) -> str:
    return json.dumps(
        {
            "subtype": "success",
            "is_error": False,
            "result": result,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "total_cost_usd": 0.01,
        }
    )


class RecordingRunner:
    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = outputs
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> Any:
        self.calls.append({"cmd": cmd, **kwargs})
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def _proc(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=""
    )


def _client(runner: RecordingRunner) -> ClaudeCliClient:
    return ClaudeCliClient(
        binary="claude",
        model="sonnet",
        timeout_s=5.0,
        config_dir="~/.claude-personal",
        runner=runner,
    )


class TestClaudeCliClient:
    def test_command_env_and_cwd_contract(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-stripped")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-should-be-stripped")
        runner = RecordingRunner([_proc(_envelope('{"x": 1}'))])
        resp = _client(runner).generate("hello")
        call = runner.calls[0]
        assert call["cmd"] == [
            "claude",
            "-p",
            "--model",
            "sonnet",
            "--output-format",
            "json",
        ]
        assert call["input"] == "hello"
        env = call["env"]
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        assert env["CLAUDE_CONFIG_DIR"].endswith(".claude-personal")
        assert "~" not in env["CLAUDE_CONFIG_DIR"]
        import os

        assert call["cwd"] != os.getcwd()  # never the repo
        assert isinstance(resp, LLMResponse)
        assert resp.text == '{"x": 1}'
        assert resp.cost_usd_notional == 0.01
        assert resp.input_tokens == 10

    def test_fenced_result_is_stripped(self) -> None:
        fenced = '```json\n{"x": 1}\n```'
        runner = RecordingRunner([_proc(_envelope(fenced))])
        assert _client(runner).generate("p").text == '{"x": 1}'

    def test_retry_once_then_success(self) -> None:
        runner = RecordingRunner([_proc("", returncode=1), _proc(_envelope("ok"))])
        assert _client(runner).generate("p").text == "ok"
        assert len(runner.calls) == 2

    def test_retry_then_carderror(self) -> None:
        runner = RecordingRunner(
            [_proc("", returncode=1), _proc("not json", returncode=0)]
        )
        with pytest.raises(CardError):
            _client(runner).generate("p")

    def test_timeout_is_carderror(self) -> None:
        exc = subprocess.TimeoutExpired(cmd="claude", timeout=5.0)
        runner = RecordingRunner([exc, exc])
        with pytest.raises(CardError, match="timeout"):
            _client(runner).generate("p")

    def test_error_envelope_is_carderror(self) -> None:
        bad = json.dumps({"subtype": "error", "is_error": True, "result": ""})
        runner = RecordingRunner([_proc(bad), _proc(bad)])
        with pytest.raises(CardError):
            _client(runner).generate("p")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_card_client.py -v`
Expected: FAIL with import error on `card.client`

- [ ] **Step 3: Write the implementation**

```python
# card/client.py
"""LLM seam: LLMClient protocol + claude-personal -p subprocess backend."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from card.errors import CardError

RunnerFn = Callable[..., "subprocess.CompletedProcess[str]"]

_STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str | None
    cost_usd_notional: float | None  # envelope total_cost_usd — NOT a charge
    input_tokens: int | None
    output_tokens: int | None


class LLMClient(Protocol):
    def generate(self, prompt: str) -> LLMResponse: ...


def _default_runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(*args, **kwargs)  # noqa: S603


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        first_nl = t.find("\n")
        t = t[first_nl + 1 :] if first_nl != -1 else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


@dataclass(frozen=True)
class ClaudeCliClient:
    """Subscription-auth `claude -p` backend.

    Load-bearing contract (see spec §client.py): keys stripped from env so
    the CLI can never bill pay-per-token; CLAUDE_CONFIG_DIR points at the
    personal config; cwd is a fresh temp dir so repo CLAUDE.md/skills are
    never reloaded into the card call.
    """

    binary: str
    model: str
    timeout_s: float
    config_dir: str
    runner: RunnerFn | None = None

    def generate(self, prompt: str) -> LLMResponse:
        last_err = "unknown"
        for _ in range(2):  # one retry per spec
            try:
                return self._call(prompt)
            except CardError as exc:
                last_err = str(exc)
        raise CardError(f"LLM call failed after retry: {last_err}")

    def _call(self, prompt: str) -> LLMResponse:
        run = self.runner if self.runner is not None else _default_runner
        cmd = [
            self.binary,
            "-p",
            "--model",
            self.model,
            "--output-format",
            "json",
        ]
        env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}
        env["CLAUDE_CONFIG_DIR"] = str(Path(self.config_dir).expanduser())
        with tempfile.TemporaryDirectory() as tmp:
            try:
                proc = run(
                    cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    cwd=tmp,
                    env=env,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise CardError(f"LLM timeout after {self.timeout_s}s") from exc
        if proc.returncode != 0:
            raise CardError(f"claude exited {proc.returncode}: {proc.stderr[:500]}")
        try:
            envelope: dict[str, Any] = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise CardError(f"bad envelope JSON: {exc}") from exc
        if envelope.get("subtype") != "success" or envelope.get("is_error"):
            raise CardError(f"envelope not success: subtype={envelope.get('subtype')}")
        result = envelope.get("result")
        if not isinstance(result, str):
            raise CardError("envelope missing result text")
        usage = envelope.get("usage") or {}
        cost = envelope.get("total_cost_usd")
        in_tok = usage.get("input_tokens")
        out_tok = usage.get("output_tokens")
        return LLMResponse(
            text=_strip_fences(result),
            model=self.model,
            cost_usd_notional=(float(cost) if isinstance(cost, (int, float)) else None),
            input_tokens=int(in_tok) if isinstance(in_tok, int) else None,
            output_tokens=int(out_tok) if isinstance(out_tok, int) else None,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_card_client.py -v`
Expected: 6 passed

- [ ] **Step 5: Gates + commit**

```bash
make lint-py && make typecheck
git add card/client.py tests/test_card_client.py
git commit -m "feat(card): ClaudeCliClient — env-guarded subscription LLM seam"
```

---

### Task 6: `card/card.py` — TradeCard schema + validation

**Files:**

- Create: `card/card.py`
- Test: `tests/test_card_card.py`

**Interfaces:**

- Consumes: `card.errors.CardValidationError`.
- Produces: `TradeCard` frozen dataclass (fields exactly as the JSON schema:
  verdict, direction, entry, sl, tp1, tp2, tp3, confluence_score, reasoning,
  invalidation, expected_hold, valid_until_utc, no_trade_reason);
  `validate_card_obj(obj: object) -> list[str]`;
  `parse_trade_card(text: str) -> TradeCard` (raises `CardValidationError`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_card_card.py
"""TradeCard parse/validation matrix (post-pass tests arrive in Task 7)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from card.card import parse_trade_card, validate_card_obj
from card.errors import CardValidationError


def _trade_obj(**overrides: Any) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "verdict": "TRADE",
        "direction": "long",
        "entry": 100.0,
        "sl": 98.0,
        "tp1": 103.0,
        "tp2": 105.0,
        "tp3": 108.0,
        "confluence_score": 6,
        "reasoning": ["a 1", "b 2", "c 3", "d 4", "e 5"],
        "invalidation": "close below 97",
        "expected_hold": "12h",
        "valid_until_utc": "2026-07-11T12:00:00Z",
        "no_trade_reason": None,
    }
    obj.update(overrides)
    return obj


class TestValidation:
    def test_valid_trade_parses(self) -> None:
        card = parse_trade_card(json.dumps(_trade_obj()))
        assert card.verdict == "TRADE"
        assert card.direction == "long"
        assert card.entry == 100.0
        assert card.reasoning[0] == "a 1"

    def test_valid_no_trade_parses(self) -> None:
        obj = _trade_obj(
            verdict="NO_TRADE",
            direction=None,
            entry=None,
            sl=None,
            tp1=None,
            tp2=None,
            tp3=None,
            no_trade_reason="regime conflict",
        )
        card = parse_trade_card(json.dumps(obj))
        assert card.verdict == "NO_TRADE"
        assert card.no_trade_reason == "regime conflict"

    def test_not_json_raises(self) -> None:
        with pytest.raises(CardValidationError):
            parse_trade_card("here is your card: buy")

    def test_bad_verdict(self) -> None:
        assert any(
            "verdict" in e for e in validate_card_obj(_trade_obj(verdict="MAYBE"))
        )

    def test_reasoning_count_bounds(self) -> None:
        assert validate_card_obj(_trade_obj(reasoning=["a"] * 4))
        assert validate_card_obj(_trade_obj(reasoning=["a"] * 9))
        assert not validate_card_obj(_trade_obj(reasoning=["a"] * 8))

    def test_confluence_bounds_and_type(self) -> None:
        assert validate_card_obj(_trade_obj(confluence_score=10))
        assert validate_card_obj(_trade_obj(confluence_score="6"))
        assert validate_card_obj(_trade_obj(confluence_score=True))

    def test_trade_requires_prices_and_direction(self) -> None:
        assert validate_card_obj(_trade_obj(entry=None))
        assert validate_card_obj(_trade_obj(sl=-1.0))
        assert validate_card_obj(_trade_obj(direction="up"))
        assert validate_card_obj(_trade_obj(valid_until_utc=None))

    def test_no_trade_requires_reason(self) -> None:
        obj = _trade_obj(verdict="NO_TRADE", no_trade_reason=None)
        assert any("no_trade_reason" in e for e in validate_card_obj(obj))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_card_card.py -v`
Expected: FAIL with import error on `card.card`

- [ ] **Step 3: Write the implementation**

```python
# card/card.py
"""TradeCard schema validation + (Task 7) the deterministic post-pass."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from card.errors import CardValidationError

_VERDICTS = ("TRADE", "NO_TRADE")
_DIRECTIONS = ("long", "short")
_PRICE_KEYS = ("entry", "sl", "tp1", "tp2", "tp3")


@dataclass(frozen=True)
class TradeCard:
    verdict: str
    direction: str | None
    entry: float | None
    sl: float | None
    tp1: float | None
    tp2: float | None
    tp3: float | None
    confluence_score: int
    reasoning: list[str]
    invalidation: str | None
    expected_hold: str | None
    valid_until_utc: str | None
    no_trade_reason: str | None


def _is_num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_card_obj(obj: object) -> list[str]:
    """Schema errors for the raw LLM JSON object; [] when valid."""
    if not isinstance(obj, dict):
        return ["card is not a JSON object"]
    errors: list[str] = []
    verdict = obj.get("verdict")
    if verdict not in _VERDICTS:
        errors.append(f"verdict must be one of {list(_VERDICTS)}")
    reasoning = obj.get("reasoning")
    if (
        not isinstance(reasoning, list)
        or not 5 <= len(reasoning) <= 8
        or not all(isinstance(b, str) for b in reasoning)
    ):
        errors.append("reasoning must be a list of 5-8 strings")
    score = obj.get("confluence_score")
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 9:
        errors.append("confluence_score must be an integer in [0, 9]")
    if verdict == "TRADE":
        if obj.get("direction") not in _DIRECTIONS:
            errors.append("TRADE requires direction 'long' or 'short'")
        for key in _PRICE_KEYS:
            v = obj.get(key)
            if not _is_num(v) or float(v) <= 0.0:  # type: ignore[arg-type]
                errors.append(f"TRADE requires positive numeric {key}")
        if not isinstance(obj.get("valid_until_utc"), str):
            errors.append("TRADE requires valid_until_utc (ISO-8601 string)")
    if verdict == "NO_TRADE" and not isinstance(obj.get("no_trade_reason"), str):
        errors.append("NO_TRADE requires no_trade_reason")
    return errors


def parse_trade_card(text: str) -> TradeCard:
    """JSON-parse + validate the LLM output; CardValidationError on failure."""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CardValidationError([f"not valid JSON: {exc}"]) from exc
    errors = validate_card_obj(obj)
    if errors:
        raise CardValidationError(errors)

    def _f(key: str) -> float | None:
        v = obj.get(key)
        return float(v) if _is_num(v) else None

    def _s(key: str) -> str | None:
        v = obj.get(key)
        return v if isinstance(v, str) else None

    return TradeCard(
        verdict=str(obj["verdict"]),
        direction=_s("direction"),
        entry=_f("entry"),
        sl=_f("sl"),
        tp1=_f("tp1"),
        tp2=_f("tp2"),
        tp3=_f("tp3"),
        confluence_score=int(obj["confluence_score"]),
        reasoning=[str(b) for b in obj["reasoning"]],
        invalidation=_s("invalidation"),
        expected_hold=_s("expected_hold"),
        valid_until_utc=_s("valid_until_utc"),
        no_trade_reason=_s("no_trade_reason"),
    )
```

(`asdict` import is used by Task 7's `FinalCard.to_dict` — if ruff flags it
now, add it in Task 7 instead.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_card_card.py -v`
Expected: 8 passed

- [ ] **Step 5: Gates + commit**

```bash
make lint-py && make typecheck
git add card/card.py tests/test_card_card.py
git commit -m "feat(card): TradeCard schema validation matrix"
```

---

### Task 7: `card/card.py` — deterministic post-pass (`FinalCard`)

**Files:**

- Modify: `card/card.py` (append)
- Test: `tests/test_card_card.py` (extend)

**Interfaces:**

- Consumes: `portfolio.sizing` — `SizingConfig`, `risk_per_unit(entry, stop)
  -> float`, `position_size(risk_capital, entry, stop) -> float`,
  `effective_risk_fraction(cfg, *, g_vol, g_regime, ...) -> float`,
  `regime_multiplier(regime_label, cfg) -> float`, `cluster_of(symbol, cfg)
  -> str`, `apply_caps(r_eff, *, symbol, open_risk_total,
  open_risk_cluster, cfg) -> float`; `card.state.MarketState`;
  `card.config.CardConfig`.
- Produces: `FinalCard(symbol, as_of_ms, verdict, card, size_units,
  notional_usd, risk_usd, risk_frac, rr_tp1, warnings, veto_reasons,
  state_digest, prompt_version, model, generated_at_ms, cost_usd_notional)`
  with `to_dict() -> dict[str, Any]`; `post_pass(card, state, sizing, cfg,
  *, digest, model, generated_at_ms, cost_usd_notional=None) -> FinalCard`.

- [ ] **Step 1: Write the failing tests (append to `tests/test_card_card.py`)**

```python
# append to tests/test_card_card.py
from card.card import FinalCard, post_pass
from card.config import CardConfig
from card.state import AccountState, MarketState, OpenPosition
from portfolio.sizing import SizingConfig


def _state_for_post(
    *,
    ref_close: float = 100.0,
    account: AccountState | None = None,
) -> MarketState:
    from analytics.brief.types import error_panel

    panel = error_panel("BTCUSDT", "x")
    # error_panel has ref_close 0.0; build a usable panel via dataclasses.replace
    import dataclasses

    panel = dataclasses.replace(panel, ref_close=ref_close, error=None)
    return MarketState(
        symbol="BTCUSDT",
        now_ms=1_760_000_000_000,
        direction_hint=None,
        panel=panel,
        session_clock=None,
        pundit=None,
        xs=None,
        recent_fires=[],
        account=account,
        health=[],
    )


def _post(card_obj: dict[str, Any], state: MarketState) -> FinalCard:
    card = parse_trade_card(json.dumps(card_obj))
    return post_pass(
        card,
        state,
        SizingConfig(),
        CardConfig(),
        digest="d" * 64,
        model="sonnet",
        generated_at_ms=1,
    )


class TestPostPass:
    def test_clean_trade_sized_deterministically(self) -> None:
        final = _post(_trade_obj(), _state_for_post())
        assert final.verdict == "TRADE"
        # r_base 0.25% of 10k = 25 USD risk; |entry-sl| = 2 -> 12.5 units
        assert final.risk_usd == 25.0
        assert final.size_units == 12.5
        assert final.notional_usd == 1250.0
        assert final.rr_tp1 == 1.5
        assert final.veto_reasons == []

    def test_no_trade_passes_through_unsized(self) -> None:
        obj = _trade_obj(
            verdict="NO_TRADE",
            direction=None,
            entry=None,
            sl=None,
            tp1=None,
            tp2=None,
            tp3=None,
            no_trade_reason="gate",
        )
        final = _post(obj, _state_for_post())
        assert final.verdict == "NO_TRADE"
        assert final.size_units is None
        assert final.risk_usd is None

    def test_sl_wrong_side_vetoes(self) -> None:
        final = _post(_trade_obj(sl=101.0), _state_for_post())
        assert final.verdict == "VETOED"
        assert any("SL" in r for r in final.veto_reasons)

    def test_tp_disorder_vetoes(self) -> None:
        final = _post(_trade_obj(tp2=102.0), _state_for_post())
        assert final.verdict == "VETOED"

    def test_min_rr_floor_vetoes(self) -> None:
        final = _post(_trade_obj(tp1=101.0), _state_for_post())  # RR 0.5
        assert final.verdict == "VETOED"
        assert any("min_rr" in r for r in final.veto_reasons)

    def test_conflicting_position_vetoes(self) -> None:
        account = AccountState(
            positions=[
                OpenPosition(
                    symbol="BTCUSDT",
                    side="short",
                    qty=1.0,
                    entry=100.0,
                    mark=100.0,
                    upnl_usd=0.0,
                )
            ],
            daily_pnl_usd=0.0,
            daily_r=0.0,
            equity_usd=None,
        )
        final = _post(_trade_obj(), _state_for_post(account=account))
        assert final.verdict == "VETOED"
        assert any("conflicting" in r for r in final.veto_reasons)

    def test_circuit_breaker_vetoes(self) -> None:
        account = AccountState(
            positions=[], daily_pnl_usd=-100.0, daily_r=-2.5, equity_usd=None
        )
        final = _post(_trade_obj(), _state_for_post(account=account))
        assert final.verdict == "VETOED"
        assert any("daily loss" in r for r in final.veto_reasons)

    def test_entry_band_vetoes(self) -> None:
        final = _post(
            _trade_obj(entry=120.0, sl=118.0, tp1=124.0, tp2=126.0, tp3=130.0),
            _state_for_post(),
        )
        assert final.verdict == "VETOED"
        assert any("entry" in r for r in final.veto_reasons)

    def test_degraded_account_warns_not_vetoes(self) -> None:
        final = _post(_trade_obj(), _state_for_post(account=None))
        assert final.verdict == "TRADE"
        assert any("account state unavailable" in w for w in final.warnings)

    def test_cluster_cap_consumes_headroom(self) -> None:
        # ETHUSDT open long is in the majors cluster with BTCUSDT:
        # cluster headroom 1% - 0.25% = 0.75% >= r_eff 0.25% -> still sized,
        # but the approximation warning is present.
        account = AccountState(
            positions=[
                OpenPosition(
                    symbol="ETHUSDT",
                    side="long",
                    qty=1.0,
                    entry=100.0,
                    mark=100.0,
                    upnl_usd=0.0,
                )
            ],
            daily_pnl_usd=0.0,
            daily_r=0.0,
            equity_usd=None,
        )
        final = _post(_trade_obj(), _state_for_post(account=account))
        assert final.verdict == "TRADE"
        assert any("approximated" in w for w in final.warnings)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_card_card.py -v`
Expected: new tests FAIL (`post_pass` not defined)

- [ ] **Step 3: Write the implementation (append to `card/card.py`)**

Add imports:

```python
from card.config import CardConfig
from card.state import MarketState
from portfolio.sizing import (
    SizingConfig,
    apply_caps,
    cluster_of,
    effective_risk_fraction,
    position_size,
    regime_multiplier,
    risk_per_unit,
)
```

Append:

```python
@dataclass(frozen=True)
class FinalCard:
    symbol: str
    as_of_ms: int
    verdict: str  # "TRADE" | "NO_TRADE" | "VETOED"
    card: TradeCard
    size_units: float | None
    notional_usd: float | None
    risk_usd: float | None
    risk_frac: float | None
    rr_tp1: float | None
    warnings: list[str]
    veto_reasons: list[str]
    state_digest: str
    prompt_version: str
    model: str
    generated_at_ms: int
    cost_usd_notional: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def post_pass(
    card: TradeCard,
    state: MarketState,
    sizing: SizingConfig,
    cfg: CardConfig,
    *,
    digest: str,
    model: str,
    generated_at_ms: int,
    cost_usd_notional: float | None = None,
) -> FinalCard:
    """The LLM proposes prices; this code decides money and rules (D6).

    Sizing notes: g_vol is neutral 1.0 (no live equity curve exists at card
    time); open risk is approximated as one r_base per open position (the
    account rows carry no SL, so true open risk is unknowable) — surfaced as
    a warning, never silent.
    """
    from card.prompt import PROMPT_VERSION

    warnings: list[str] = []
    veto: list[str] = []
    size_units: float | None = None
    notional_usd: float | None = None
    risk_usd: float | None = None
    risk_frac: float | None = None
    rr_tp1: float | None = None

    if card.verdict == "TRADE":
        # validation guarantees these are positive floats for TRADE
        entry = float(card.entry or 0.0)
        sl = float(card.sl or 0.0)
        direction = card.direction or ""
        tps = [float(t) for t in (card.tp1, card.tp2, card.tp3) if t is not None]

        # (a) SL side + TP ordering
        if direction == "long" and sl >= entry:
            veto.append("SL must be below entry for a long")
        if direction == "short" and sl <= entry:
            veto.append("SL must be above entry for a short")
        expected = sorted(tps) if direction == "long" else sorted(tps, reverse=True)
        if tps != expected:
            veto.append("TPs must be ordered away from entry")
        if tps and direction == "long" and tps[0] <= entry:
            veto.append("tp1 must be above entry for a long")
        if tps and direction == "short" and tps[0] >= entry:
            veto.append("tp1 must be below entry for a short")

        # (b) planned RR floor
        rpu = risk_per_unit(entry, sl)
        if rpu > 0.0 and card.tp1 is not None:
            rr_tp1 = abs(float(card.tp1) - entry) / rpu
            if rr_tp1 < cfg.min_rr:
                veto.append(f"rr_tp1 {rr_tp1:.2f} breaches min_rr {cfg.min_rr}")

        # (c) conflicting open position + (d) circuit breaker
        if state.account is not None:
            for pos in state.account.positions:
                if pos.symbol == state.symbol and pos.side != direction:
                    veto.append(f"conflicting open {pos.side} position on {pos.symbol}")
            if state.account.daily_r <= cfg.daily_loss_limit_r:
                veto.append(
                    f"daily loss {state.account.daily_r:.2f}R breaches "
                    f"circuit breaker {cfg.daily_loss_limit_r}R"
                )
        else:
            warnings.append("account state unavailable — hard rules unverified")

        # (e) entry sanity band vs ref_close
        panel = state.panel
        if panel is not None and panel.error is None and panel.ref_close > 0.0:
            band = cfg.entry_band_pct / 100.0
            if abs(entry - panel.ref_close) / panel.ref_close > band:
                veto.append(
                    f"entry {entry} outside ±{cfg.entry_band_pct}% of "
                    f"ref_close {panel.ref_close}"
                )
        else:
            warnings.append("ref price unavailable — entry sanity unverified")

        # sizing (P1 reuse) — only when nothing vetoed
        if not veto:
            regime = panel.regime_1d if panel is not None else None
            r_eff = effective_risk_fraction(
                sizing,
                g_vol=1.0,
                g_regime=regime_multiplier(regime, sizing),
            )
            open_risk_total = 0.0
            open_risk_cluster = 0.0
            if state.account is not None and state.account.positions:
                cluster = cluster_of(state.symbol, sizing)
                open_risk_total = len(state.account.positions) * sizing.r_base
                open_risk_cluster = sum(
                    sizing.r_base
                    for p in state.account.positions
                    if cluster_of(p.symbol, sizing) == cluster
                )
                warnings.append(
                    "open risk approximated as one r_base per open position"
                )
            r_adm = apply_caps(
                r_eff,
                symbol=state.symbol,
                open_risk_total=open_risk_total,
                open_risk_cluster=open_risk_cluster,
                cfg=sizing,
            )
            if r_adm <= 0.0:
                veto.append("no risk headroom under concurrent/cluster caps")
            else:
                risk_frac = r_adm
                risk_usd = sizing.capital * r_adm
                size_units = position_size(risk_usd, entry, sl)
                notional_usd = size_units * entry

    verdict = "VETOED" if veto else card.verdict
    if veto:
        size_units = notional_usd = risk_usd = risk_frac = None
    return FinalCard(
        symbol=state.symbol,
        as_of_ms=state.now_ms,
        verdict=verdict,
        card=card,
        size_units=size_units,
        notional_usd=notional_usd,
        risk_usd=risk_usd,
        risk_frac=risk_frac,
        rr_tp1=rr_tp1,
        warnings=warnings,
        veto_reasons=veto,
        state_digest=digest,
        prompt_version=PROMPT_VERSION,
        model=model,
        generated_at_ms=generated_at_ms,
        cost_usd_notional=cost_usd_notional,
    )
```

(The `from card.prompt import PROMPT_VERSION` import is function-local to
avoid a module cycle `prompt -> state`, `card -> prompt`; if mypy/ruff are
happy with a top-level import — there is no true cycle since prompt imports
state, not card — hoist it to the top instead. Verify with
`make typecheck`; prefer top-level if it passes.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_card_card.py -v`
Expected: all pass (Task 6's 8 + these 10)

- [ ] **Step 5: Gates + commit**

```bash
make lint-py && make typecheck
git add card/card.py tests/test_card_card.py
git commit -m "feat(card): deterministic post-pass — sizing + hard rules in code"
```

---

### Task 8: `card/ledger.py` — dual-write, pundit-scorer compatible

**Files:**

- Create: `card/ledger.py`
- Test: `tests/test_card_ledger.py`

**Interfaces:**

- Consumes: `card.card.FinalCard`, `card.config.CardConfig`.
- Produces: `pundit_row(final: FinalCard) -> dict[str, str]`;
  `append_ledgers(final: FinalCard, cfg: CardConfig) -> list[Path]`
  (ai-cards row always; pundit-calls row iff `final.verdict == "TRADE"`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_card_ledger.py
"""Ledger: ai-cards round-trip, TRADE-only dual-write, scorer compatibility."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from card.card import FinalCard, parse_trade_card
from card.config import CardConfig
from card.ledger import append_ledgers, pundit_row


def _final(verdict: str = "TRADE") -> FinalCard:
    obj: dict[str, Any] = {
        "verdict": "TRADE",
        "direction": "long",
        "entry": 100.0,
        "sl": 98.0,
        "tp1": 103.0,
        "tp2": 105.0,
        "tp3": 108.0,
        "confluence_score": 6,
        "reasoning": ["ref_close 100 above POC 99", "b", "c", "d", "e"],
        "invalidation": "close below 97",
        "expected_hold": "12h",
        "valid_until_utc": "2026-07-11T12:00:00Z",
        "no_trade_reason": None,
    }
    return FinalCard(
        symbol="BTCUSDT",
        as_of_ms=1_760_000_000_000,
        verdict=verdict,
        card=parse_trade_card(json.dumps(obj)),
        size_units=12.5,
        notional_usd=1250.0,
        risk_usd=25.0,
        risk_frac=0.0025,
        rr_tp1=1.5,
        warnings=[],
        veto_reasons=[] if verdict != "VETOED" else ["x"],
        state_digest="d" * 64,
        prompt_version="card-v1",
        model="sonnet",
        generated_at_ms=1_760_000_100_000,
        cost_usd_notional=0.01,
    )


def _cfg(tmp_path: Path) -> CardConfig:
    return CardConfig(
        cards_path=str(tmp_path / "ai-cards.jsonl"),
        pundit_calls_path=str(tmp_path / "pundit-calls.jsonl"),
    )


class TestLedger:
    def test_trade_dual_writes(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        paths = append_ledgers(_final("TRADE"), cfg)
        assert len(paths) == 2
        cards = Path(cfg.cards_path).read_text().strip().splitlines()
        assert len(cards) == 1
        row = json.loads(cards[0])
        assert row["symbol"] == "BTCUSDT"
        assert row["state_digest"] == "d" * 64
        assert row["card"]["entry"] == 100.0

    def test_no_trade_and_vetoed_write_cards_only(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        assert len(append_ledgers(_final("NO_TRADE"), cfg)) == 1
        assert len(append_ledgers(_final("VETOED"), cfg)) == 1
        assert not Path(cfg.pundit_calls_path).exists()

    def test_pundit_row_parses_through_scorer_loader(self, tmp_path: Path) -> None:
        from tools.pundit_score import load_ledger

        cfg = _cfg(tmp_path)
        append_ledgers(_final("TRADE"), cfg)
        calls, warnings = load_ledger(Path(cfg.pundit_calls_path))
        assert warnings == []
        assert len(calls) == 1
        call = calls[0]
        assert call.source == "ai-card"
        assert call.author == "buibui_card"
        assert call.symbol == "BTCUSDT"
        assert call.direction == "long"
        assert call.horizon == "intraday"  # confirmed WINDOWS_MS key
        assert call.url.startswith("ai-card://")
        assert call.entry == "100.0"
        assert call.stop == "98.0"
        assert call.target == "103.0"

    def test_url_unique_per_generation(self, tmp_path: Path) -> None:
        a = pundit_row(_final("TRADE"))
        b = dict(a)
        assert a["url"] == "ai-card://1760000100000-BTCUSDT"
        assert a["raw_quote"] == "ref_close 100 above POC 99"
        assert b["call_ts_utc"].endswith("Z")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_card_ledger.py -v`
Expected: FAIL with import error on `card.ledger`

- [ ] **Step 3: Write the implementation**

```python
# card/ledger.py
"""Dual ledger writer: ai-cards.jsonl (all) + pundit-calls.jsonl (TRADE)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from card.card import FinalCard
from card.config import CardConfig


def pundit_row(final: FinalCard) -> dict[str, str]:
    """Pundit-scorer-compatible row (the loader's exact 12 free-text keys).

    horizon uses the scorer's shortest pre-committed WINDOWS_MS key
    ("intraday" = 48h); unknown keys silently fall back to "unspecified",
    so never invent new ones.
    """
    card = final.card
    ts = datetime.fromtimestamp(final.as_of_ms / 1000, tz=UTC)
    return {
        "source": "ai-card",
        "author": "buibui_card",
        "url": f"ai-card://{final.generated_at_ms}-{final.symbol}",
        "call_ts_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol": final.symbol,
        "direction": card.direction or "",
        "entry": str(card.entry),
        "stop": str(card.sl),
        "target": str(card.tp1),
        "horizon": "intraday",
        "confidence": str(card.confluence_score),
        "raw_quote": card.reasoning[0] if card.reasoning else "",
    }


def _append_line(path: Path, obj: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, sort_keys=True) + "\n")


def append_ledgers(final: FinalCard, cfg: CardConfig) -> list[Path]:
    """Every card -> ai-cards.jsonl; TRADE cards also -> pundit-calls.jsonl."""
    cards_path = Path(cfg.cards_path)
    _append_line(cards_path, final.to_dict())
    written = [cards_path]
    if final.verdict == "TRADE":
        calls_path = Path(cfg.pundit_calls_path)
        _append_line(calls_path, dict(pundit_row(final)))
        written.append(calls_path)
    return written
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_card_ledger.py -v`
Expected: 4 passed

- [ ] **Step 5: Gates + commit**

```bash
make lint-py && make typecheck
git add card/ledger.py tests/test_card_ledger.py
git commit -m "feat(card): dual ledger — scoreable pundit rows with zero scorer changes"
```

---

### Task 9: `card/render.py` — terminal renderer

**Files:**

- Create: `card/render.py`
- Test: `tests/test_card_render.py`

**Interfaces:**

- Consumes: `card.card.FinalCard`.
- Produces: `render_card(final: FinalCard) -> str` (deterministic markdown).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_card_render.py
"""Golden-string renderer tests on fixed FinalCards."""

from __future__ import annotations

import json
from typing import Any

from card.card import FinalCard, parse_trade_card
from card.render import render_card


def _final(verdict: str, **card_overrides: Any) -> FinalCard:
    obj: dict[str, Any] = {
        "verdict": "TRADE" if verdict != "NO_TRADE" else "NO_TRADE",
        "direction": "long" if verdict != "NO_TRADE" else None,
        "entry": 100.0 if verdict != "NO_TRADE" else None,
        "sl": 98.0 if verdict != "NO_TRADE" else None,
        "tp1": 103.0 if verdict != "NO_TRADE" else None,
        "tp2": 105.0 if verdict != "NO_TRADE" else None,
        "tp3": 108.0 if verdict != "NO_TRADE" else None,
        "confluence_score": 6,
        "reasoning": ["a 1", "b 2", "c 3", "d 4", "e 5"],
        "invalidation": "close below 97",
        "expected_hold": "12h",
        "valid_until_utc": "2026-07-11T12:00:00Z",
        "no_trade_reason": None if verdict != "NO_TRADE" else "regime conflict",
    }
    obj.update(card_overrides)
    return FinalCard(
        symbol="BTCUSDT",
        as_of_ms=1_760_000_000_000,
        verdict=verdict,
        card=parse_trade_card(json.dumps(obj)),
        size_units=12.5 if verdict == "TRADE" else None,
        notional_usd=1250.0 if verdict == "TRADE" else None,
        risk_usd=25.0 if verdict == "TRADE" else None,
        risk_frac=0.0025 if verdict == "TRADE" else None,
        rr_tp1=1.5 if verdict != "NO_TRADE" else None,
        warnings=["open risk approximated as one r_base per open position"],
        veto_reasons=["SL must be below entry for a long"]
        if verdict == "VETOED"
        else [],
        state_digest="d" * 64,
        prompt_version="card-v1",
        model="sonnet",
        generated_at_ms=1,
        cost_usd_notional=0.0123,
    )


class TestRender:
    def test_trade_card_layout(self) -> None:
        out = render_card(_final("TRADE"))
        assert "▲ TRADE" in out
        assert "BTCUSDT" in out
        assert "entry 100.0" in out
        assert "SL 98.0" in out
        assert "TP1 103.0" in out
        assert "RR(tp1) 1.50" in out
        assert "12.5 units" in out
        assert "risk $25.00" in out
        assert "confluence 6/9" in out
        assert "⚠ open risk approximated" in out
        assert "card-v1" in out
        assert "dddddddd" in out  # digest short-hash

    def test_no_trade_layout(self) -> None:
        out = render_card(_final("NO_TRADE"))
        assert "─ NO TRADE" in out
        assert "regime conflict" in out
        assert "entry" not in out.split("\n")[0]

    def test_vetoed_layout(self) -> None:
        out = render_card(_final("VETOED"))
        assert "✕ VETOED" in out
        assert "SL must be below entry" in out

    def test_deterministic(self) -> None:
        assert render_card(_final("TRADE")) == render_card(_final("TRADE"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_card_render.py -v`
Expected: FAIL with import error on `card.render`

- [ ] **Step 3: Write the implementation**

```python
# card/render.py
"""Deterministic terminal renderer (brief render.py pattern)."""

from __future__ import annotations

from card.card import FinalCard

_BANNERS = {"TRADE": "▲ TRADE", "NO_TRADE": "─ NO TRADE", "VETOED": "✕ VETOED"}


def render_card(final: FinalCard) -> str:
    card = final.card
    lines = [f"BUIBUI TRADE CARD — {final.symbol} · {_BANNERS[final.verdict]}"]
    for reason in final.veto_reasons:
        lines.append(f"  veto: {reason}")
    if final.verdict == "NO_TRADE" and card.no_trade_reason:
        lines.append(f"  gate: {card.no_trade_reason}")
    if card.verdict == "TRADE":
        rr = f"{final.rr_tp1:.2f}" if final.rr_tp1 is not None else "?"
        lines.append(
            f"{card.direction} · entry {card.entry} · SL {card.sl} · RR(tp1) {rr}"
        )
        lines.append(f"TP1 {card.tp1} · TP2 {card.tp2} · TP3 {card.tp3}")
    if final.size_units is not None:
        lines.append(
            f"size: {final.size_units} units · notional "
            f"${final.notional_usd:.2f} · risk ${final.risk_usd:.2f} "
            f"({(final.risk_frac or 0.0) * 100:.2f}% of capital)"
        )
    lines.append(f"confluence {card.confluence_score}/9")
    lines.append("reasoning:")
    lines.extend(f"  - {b}" for b in card.reasoning)
    if card.invalidation:
        lines.append(f"invalidation: {card.invalidation}")
    if card.expected_hold or card.valid_until_utc:
        lines.append(
            f"hold {card.expected_hold or '?'} · valid until "
            f"{card.valid_until_utc or '?'}"
        )
    lines.extend(f"⚠ {w}" for w in final.warnings)
    cost = (
        f"${final.cost_usd_notional:.4f} notional"
        if final.cost_usd_notional is not None
        else "n/a"
    )
    lines.append(
        f"[{final.model} · {final.prompt_version} · "
        f"state {final.state_digest[:8]} · cost {cost}]"
    )
    return "\n".join(lines)
```

(Adjust exact literals until the Step 1 assertions pass — the assertions are
the contract; e.g. `size: 12.5 units` satisfies `"12.5 units" in out`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_card_render.py -v`
Expected: 4 passed

- [ ] **Step 5: Gates + commit**

```bash
make lint-py && make typecheck
git add card/render.py tests/test_card_render.py
git commit -m "feat(card): deterministic terminal card renderer"
```

---

### Task 10: `card/run.py` — orchestrator (LLM → parse → re-ask → post-pass)

**Files:**

- Create: `card/run.py`
- Test: `tests/test_card_run.py`

**Interfaces:**

- Consumes: everything above — `build_prompt`, `LLMClient`/`LLMResponse`,
  `parse_trade_card`, `post_pass`, `state_digest`, `CardValidationError`.
- Produces: `generate_card(state: MarketState, cfg: CardConfig,
  sizing: SizingConfig, client: LLMClient, *, generated_at_ms: int)
  -> FinalCard`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_card_run.py
"""Orchestrator: happy path, one re-ask on validation failure, then error."""

from __future__ import annotations

import json
from typing import Any

import pytest

from card.client import LLMResponse
from card.config import CardConfig
from card.errors import CardValidationError
from card.run import generate_card
from card.state import MarketState
from portfolio.sizing import SizingConfig


def _state() -> MarketState:
    return MarketState(
        symbol="BTCUSDT",
        now_ms=1_760_000_000_000,
        direction_hint=None,
        panel=None,
        session_clock=None,
        pundit=None,
        xs=None,
        recent_fires=[],
        account=None,
        health=[],
    )


_VALID = json.dumps(
    {
        "verdict": "NO_TRADE",
        "direction": None,
        "entry": None,
        "sl": None,
        "tp1": None,
        "tp2": None,
        "tp3": None,
        "confluence_score": 2,
        "reasoning": ["a 1", "b 2", "c 3", "d 4", "e 5"],
        "invalidation": None,
        "expected_hold": None,
        "valid_until_utc": None,
        "no_trade_reason": "chop regime",
    }
)


class FakeClient:
    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> LLMResponse:
        self.prompts.append(prompt)
        return LLMResponse(
            text=self.texts.pop(0),
            model="sonnet",
            cost_usd_notional=0.01,
            input_tokens=1,
            output_tokens=1,
        )


class TestGenerateCard:
    def test_happy_path(self) -> None:
        client = FakeClient([_VALID])
        final = generate_card(
            _state(), CardConfig(), SizingConfig(), client, generated_at_ms=7
        )
        assert final.verdict == "NO_TRADE"
        assert final.generated_at_ms == 7
        assert final.model == "sonnet"
        assert final.cost_usd_notional == 0.01
        assert len(final.state_digest) == 64
        assert len(client.prompts) == 1

    def test_reask_appends_validation_errors(self) -> None:
        client = FakeClient(["not json at all", _VALID])
        final = generate_card(
            _state(), CardConfig(), SizingConfig(), client, generated_at_ms=7
        )
        assert final.verdict == "NO_TRADE"
        assert len(client.prompts) == 2
        assert "failed validation" in client.prompts[1]

    def test_second_failure_raises(self) -> None:
        client = FakeClient(["nope", "still nope"])
        with pytest.raises(CardValidationError):
            generate_card(
                _state(),
                CardConfig(),
                SizingConfig(),
                client,
                generated_at_ms=7,
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_card_run.py -v`
Expected: FAIL with import error on `card.run`

- [ ] **Step 3: Write the implementation**

```python
# card/run.py
"""Orchestrator: prompt -> LLM -> parse (one re-ask) -> post-pass."""

from __future__ import annotations

from card.card import FinalCard, parse_trade_card, post_pass
from card.client import LLMClient
from card.config import CardConfig
from card.errors import CardValidationError
from card.prompt import build_prompt
from card.state import MarketState, state_digest
from portfolio.sizing import SizingConfig


def generate_card(
    state: MarketState,
    cfg: CardConfig,
    sizing: SizingConfig,
    client: LLMClient,
    *,
    generated_at_ms: int,
) -> FinalCard:
    """One card, end to end. Raises CardError/CardValidationError on failure
    (nothing is written on failure — the CLI only persists a returned card)."""
    prompt = build_prompt(state, cfg)
    response = client.generate(prompt)
    try:
        card = parse_trade_card(response.text)
    except CardValidationError as exc:
        retry_prompt = (
            prompt
            + "\n\nYour previous response failed validation:\n- "
            + "\n- ".join(exc.errors)
            + "\nRespond again with ONLY a corrected JSON object."
        )
        response = client.generate(retry_prompt)
        card = parse_trade_card(response.text)  # second failure propagates
    return post_pass(
        card,
        state,
        sizing,
        cfg,
        digest=state_digest(state),
        model=response.model or cfg.model,
        generated_at_ms=generated_at_ms,
        cost_usd_notional=response.cost_usd_notional,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_card_run.py -v`
Expected: 3 passed

- [ ] **Step 5: Gates + commit**

```bash
make lint-py && make typecheck
git add card/run.py tests/test_card_run.py
git commit -m "feat(card): generate_card orchestrator with one re-ask"
```

---

### Task 11: CLI wiring — `cli/card.py`, `cli/main.py`, Makefile, re-exports

**Files:**

- Create: `cli/card.py`
- Modify: `cli/main.py` (import list + one registration line)
- Modify: `card/__init__.py` (public re-exports)
- Modify: `Makefile` (one target, next to `buibui-brief`)
- Test: `tests/test_cli_card.py`

**Interfaces:**

- Consumes: everything above; `analytics.brief._common.parse_as_of_ms`;
  `analytics.data_store.DEFAULT_DB_PATH`; `utils.binance_client.create_client`.
- Produces: `buibui card SYMBOL [--direction long|short] [--as-of ISO]
  [--db PATH] [--config PATH] [--json] [--dry-run] [--no-ledger]`;
  `make buibui-card SYMBOL=BTCUSDT [DIRECTION=long] [AS_OF=...] [DRY=1]`;
  `BinanceAccountProvider` (CLI-layer, satisfies `AccountProvider`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_card.py
"""CLI: parser registration, dry-run path, BinanceAccountProvider mapping."""

from __future__ import annotations

import argparse
from typing import Any
from unittest.mock import MagicMock

from cli.card import BinanceAccountProvider, add_card_subparser


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    add_card_subparser(subparsers)
    return parser.parse_args(argv)


class TestParser:
    def test_defaults(self) -> None:
        args = _parse(["card", "BTCUSDT"])
        assert args.symbol == "BTCUSDT"
        assert args.direction is None
        assert args.as_of is None
        assert args.dry_run is False
        assert args.no_ledger is False
        assert args.json is False
        assert callable(args.func)

    def test_flags(self) -> None:
        args = _parse(
            [
                "card",
                "ETHUSDT",
                "--direction",
                "short",
                "--as-of",
                "2026-07-11T00:00:00Z",
                "--dry-run",
                "--no-ledger",
                "--json",
            ]
        )
        assert args.direction == "short"
        assert args.dry_run is True


class TestBinanceAccountProvider:
    def _client(self) -> Any:
        client = MagicMock()
        client.futures_position_information.return_value = [
            {
                "symbol": "BTCUSDT",
                "positionAmt": "-0.5",
                "entryPrice": "100.0",
                "markPrice": "99.0",
                "unRealizedProfit": "0.5",
            },
            {"symbol": "ETHUSDT", "positionAmt": "0"},
        ]
        client.futures_income_history.return_value = [
            {"incomeType": "REALIZED_PNL", "income": "-40.0"},
            {"incomeType": "COMMISSION", "income": "-1.5"},
            {"incomeType": "FUNDING_FEE", "income": "0.5"},
            {"incomeType": "TRANSFER", "income": "999.0"},  # excluded
        ]
        client.futures_account_balance.return_value = [
            {"asset": "BNB", "balance": "1.0"},
            {"asset": "USDT", "balance": "9000.0"},
        ]
        return client

    def test_positions_maps_nonzero_only(self) -> None:
        provider = BinanceAccountProvider(self._client())
        positions = provider.positions()
        assert len(positions) == 1
        assert positions[0].symbol == "BTCUSDT"
        assert positions[0].side == "short"
        assert positions[0].qty == 0.5

    def test_daily_pnl_filters_income_types(self) -> None:
        provider = BinanceAccountProvider(self._client())
        assert provider.daily_pnl_usd(0, 1) == -41.0

    def test_equity_reads_usdt(self) -> None:
        provider = BinanceAccountProvider(self._client())
        assert provider.equity_usd() == 9000.0


class TestDryRun:
    def test_dry_run_prints_state_and_prompt_no_llm(
        self, capsys: Any, tmp_path: Any, monkeypatch: Any
    ) -> None:
        import duckdb

        from analytics.store.schema import init_schema
        from cli.card import run_card_cmd

        db = tmp_path / "t.db"
        conn = duckdb.connect(str(db))
        init_schema(conn)
        conn.close()
        args = argparse.Namespace(
            symbol="BTCUSDT",
            direction=None,
            as_of="2026-07-11T00:00:00Z",
            db=str(db),
            config=None,
            json=False,
            dry_run=True,
            no_ledger=False,
        )
        run_card_cmd(args)
        out = capsys.readouterr().out
        assert '"symbol": "BTCUSDT"' in out
        assert "MARKET STATE JSON" in out  # the prompt was printed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_cli_card.py -v`
Expected: FAIL with import error on `cli.card`

- [ ] **Step 3: Write the implementation**

```python
# cli/card.py
"""Buibui CLI — `card` subcommand (AI trade card; advisory, routes no orders)."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

import duckdb

from analytics.brief._common import parse_as_of_ms
from analytics.data_store import DEFAULT_DB_PATH
from card.client import ClaudeCliClient
from card.config import CardConfig
from card.ledger import append_ledgers
from card.prompt import build_prompt
from card.render import render_card
from card.run import generate_card
from card.state import AccountProvider, OpenPosition, snapshot_market_state
from portfolio.sizing import SizingConfig

_INCOME_TYPES = {"REALIZED_PNL", "COMMISSION", "FUNDING_FEE"}


class BinanceAccountProvider:
    """AccountProvider over the raw python-binance futures client (reads only)."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def positions(self) -> list[OpenPosition]:
        out: list[OpenPosition] = []
        for p in self._client.futures_position_information():
            amt = float(p.get("positionAmt", 0) or 0)
            if amt == 0.0:
                continue
            out.append(
                OpenPosition(
                    symbol=str(p["symbol"]),
                    side="long" if amt > 0 else "short",
                    qty=abs(amt),
                    entry=float(p.get("entryPrice", 0) or 0),
                    mark=float(p.get("markPrice", 0) or 0),
                    upnl_usd=float(p.get("unRealizedProfit", 0) or 0),
                )
            )
        return out

    def daily_pnl_usd(self, start_ms: int, end_ms: int) -> float:
        rows = self._client.futures_income_history(
            startTime=start_ms, endTime=end_ms, limit=1000
        )
        return float(
            sum(
                float(r.get("income", 0) or 0)
                for r in rows
                if r.get("incomeType") in _INCOME_TYPES
            )
        )

    def equity_usd(self) -> float | None:
        try:
            for b in self._client.futures_account_balance():
                if b.get("asset") == "USDT":
                    return float(b.get("balance", 0) or 0)
        except Exception:
            return None
        return None


def _build_account_provider() -> AccountProvider | None:
    """Real provider, or None (degraded, warned) when keys/network absent."""
    try:
        from utils.binance_client import create_client

        return BinanceAccountProvider(create_client())
    except Exception:
        return None


def run_card_cmd(args: argparse.Namespace) -> None:
    cfg = CardConfig.from_toml(args.config) if args.config else CardConfig()
    sizing = (
        SizingConfig.from_toml(cfg.sizing_toml) if cfg.sizing_toml else SizingConfig()
    )
    now_ms = parse_as_of_ms(args.as_of) if args.as_of else int(time.time() * 1000)
    provider = None if args.dry_run else _build_account_provider()
    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        state = snapshot_market_state(
            conn,
            args.symbol,
            cfg,
            sizing,
            now_ms=now_ms,
            account_provider=provider,
            direction_hint=args.direction,
        )
    finally:
        conn.close()

    if args.dry_run:
        print(json.dumps(state.to_dict(), indent=2, sort_keys=True))
        print("\n----- PROMPT (no LLM call made) -----\n")
        print(build_prompt(state, cfg))
        return

    client = ClaudeCliClient(
        binary=cfg.claude_bin,
        model=cfg.model,
        timeout_s=cfg.timeout_s,
        config_dir=cfg.claude_config_dir,
    )
    final = generate_card(
        state, cfg, sizing, client, generated_at_ms=int(time.time() * 1000)
    )
    if args.json:
        print(json.dumps(final.to_dict(), indent=2, sort_keys=True))
    else:
        print(render_card(final))
    if not args.no_ledger:
        for path in append_ledgers(final, cfg):
            print(f"ledger: {path}")


def add_card_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    p = subparsers.add_parser(
        "card",
        help="AI trade card for one symbol (advisory; routes no orders)",
    )
    p.add_argument("symbol", help="e.g. BTCUSDT")
    p.add_argument("--direction", choices=["long", "short"], default=None)
    p.add_argument(
        "--as-of",
        dest="as_of",
        default=None,
        help="ISO8601 anchor for reproducible inputs (default: now)",
    )
    p.add_argument("--db", default=str(DEFAULT_DB_PATH), help="DuckDB path")
    p.add_argument("--config", default=None, help="TOML with a [card] block")
    p.add_argument("--json", action="store_true", help="emit FinalCard JSON")
    p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="print state + prompt, skip the LLM call (free smoke test)",
    )
    p.add_argument(
        "--no-ledger",
        dest="no_ledger",
        action="store_true",
        help="skip ledger persistence (exploration)",
    )
    p.set_defaults(func=run_card_cmd)
```

Modify `cli/main.py`: add `card` to the `from cli import (...)` tuple
(alphabetical position after `brief`) and register it right after brief:

```python
    brief.add_brief_subparser(subparsers)
    card.add_card_subparser(subparsers)
```

Update `card/__init__.py`:

```python
# card/__init__.py
"""AI trade-card package (F2): advisory cards, no order routing."""

from card.card import FinalCard, TradeCard, parse_trade_card, post_pass
from card.client import ClaudeCliClient, LLMClient, LLMResponse
from card.config import CardConfig
from card.errors import CardError, CardValidationError
from card.ledger import append_ledgers, pundit_row
from card.prompt import PROMPT_VERSION, build_prompt
from card.render import render_card
from card.run import generate_card
from card.state import (
    AccountProvider,
    AccountState,
    MarketState,
    OpenPosition,
    RecentFire,
    snapshot_market_state,
    state_digest,
)

__all__ = [
    "PROMPT_VERSION",
    "AccountProvider",
    "AccountState",
    "CardConfig",
    "CardError",
    "CardValidationError",
    "ClaudeCliClient",
    "FinalCard",
    "LLMClient",
    "LLMResponse",
    "MarketState",
    "OpenPosition",
    "RecentFire",
    "TradeCard",
    "append_ledgers",
    "build_prompt",
    "generate_card",
    "parse_trade_card",
    "post_pass",
    "pundit_row",
    "render_card",
    "snapshot_market_state",
    "state_digest",
]
```

Add to `Makefile` directly below the `buibui-brief` target (match its
style; recipe lines below are shown space-indented for the linter — **use
real TAB indentation in the Makefile**, like every neighboring target):

```make
.PHONY: buibui-card
buibui-card:  ## AI trade card (SYMBOL= required; DIRECTION=/AS_OF=/DRY=1 optional)
    @poetry run python buibui.py card $(SYMBOL) \
        $(if $(DIRECTION),--direction $(DIRECTION),) \
        $(if $(AS_OF),--as-of $(AS_OF),) \
        $(if $(DRY),--dry-run,)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_cli_card.py -v`
Expected: 6 passed

Also verify the parser registers end-to-end:

Run: `poetry run python buibui.py card --help`
Expected: usage text with all 7 flags, exit 0

- [ ] **Step 5: Gates + commit**

```bash
make lint-py && make typecheck
git add cli/card.py cli/main.py card/__init__.py Makefile tests/test_cli_card.py
git commit -m "feat(card): buibui card CLI + Makefile target + package exports"
```

---

### Task 12: Docs + full Definition-of-Done sweep

**Files:**

- Modify: `README.md` (CLI section: add `buibui card`; project structure:
  add `card/`)
- Modify: `CLAUDE.md` (CLI list: one `buibui card` bullet; Project
  Structure: one `card/` package bullet describing the 9 modules + the
  spec/addendum pointer + "advisory only, no order routing")
- No new code.

- [ ] **Step 1: Update README.md and CLAUDE.md**

CLAUDE.md CLI bullet (place after `buibui brief`):

```text
- `buibui card SYMBOL` — AI trade card (F2): composes brief panel (incl. M1
  indicators + M2 sessions) + pundit board + XS target + recent fires +
  live account into a MarketState, sends the card-v1 rubric to
  `claude -p` (subscription auth, keys stripped,
  CLAUDE_CONFIG_DIR=~/.claude-personal, bare temp cwd), then a
  deterministic post-pass sizes the trade (P1 sizing reuse) and enforces
  hard rules in code (VETOED on violation). Every card appends to
  gitignored `docs/plans/ai-cards.jsonl`; TRADE cards dual-write a
  pundit-calls row (author `buibui_card`, horizon `intraday`) so
  `make buibui-pundit-score` scores the AI with zero scorer changes.
  `--dry-run` prints state + prompt with no LLM call. Wrapped by
  `make buibui-card SYMBOL=BTCUSDT [DIRECTION=] [AS_OF=] [DRY=1]`.
```

CLAUDE.md Project Structure bullet (peer of `portfolio/` / `trade/`):

```text
- `card/` — F2 AI trade-card package (spec
  `docs/superpowers/specs/2026-07-08-f2-trade-card-design.md` + 2026-07-11
  addendum): `config.py` (`CardConfig.from_toml` `[card]` block), `state.py`
  (`snapshot_market_state` — composes brief bundle (panel incl.
  indicators/sessions, session_clock, PunditBoard) + XS target row + recent
  fires annotated from `confidence_ratings` via the additive
  `get_confidence_rating_rows` getter + injected `AccountProvider`;
  per-block failure isolation; `state_digest` sha256), `prompt.py`
  (`PROMPT_VERSION="card-v1"`, byte-stable rubric), `client.py`
  (`LLMClient` protocol + `ClaudeCliClient` subprocess backend — env-strip
  + personal config dir + temp cwd are load-bearing), `card.py`
  (`TradeCard` validation + `post_pass` deterministic sizing/hard rules →
  `FinalCard`), `run.py` (orchestrator, one re-ask), `ledger.py`
  (ai-cards.jsonl + TRADE-only pundit-calls dual-write), `render.py`,
  `errors.py`; `cli/card.py` builds the real Binance provider. Advisory
  only — no order routing.
```

README: mirror the CLI addition in whatever section lists subcommands
(match surrounding formatting exactly).

- [ ] **Step 2: Run the full gate suite and state each result plainly**

```bash
make lint-py
make typecheck
make test
make test-regression
make lint-md
```

Expected: all green; regression goldens byte-unmoved (this plan touches no
detector/backtest/stats path). Known pre-existing flakiness: intermittent
`test_regression` timeouts under the full suite — rerun standalone
(`make test-regression`) before concluding anything moved.

- [ ] **Step 3: Manual smoke (no LLM cost)**

```bash
make buibui-card SYMBOL=BTCUSDT DRY=1
```

Expected: state JSON (panel/xs/fires/account blocks with health notes for
whatever is absent locally) + the full prompt, no LLM call, exit 0.

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: card/ package + buibui card CLI reference"
```

---

## Self-Review Notes (already applied)

- **Spec coverage:** config ✓ (T1) · state/serialiser ✓ (T2–T3) · prompt ✓
  (T4) · client ✓ (T5) · schema+post-pass ✓ (T6–T7) · ledger ✓ (T8) ·
  render ✓ (T9) · orchestrator/re-ask ✓ (T10) · CLI/Makefile ✓ (T11) ·
  docs/DoD ✓ (T12). Error-handling table: block-fail → health note (T3),
  provider-fail → degraded warning (T3/T7), CLI-missing/timeout/envelope →
  CardError (T5), schema-fail → one re-ask (T10), rule violation → VETOED
  (T7).
- **Addendum deltas honored:** pundit block = `PunditBoard` (T3);
  fires annotation = stars/avg_r/win_rate/dsr, no `n` (T3); horizon =
  `"intraday"` (T8); `card/run.py` + `card/errors.py` exist (T1/T10);
  `LLMResponse` typed cost (T5); `snapshot_market_state` takes `sizing` +
  `brief_fn`/`targets_fn` seams (T3).
- **Live-run caveat for the executor:** a real (non-dry) `buibui card` run
  is deliberately NOT part of any task — it consumes plan tokens and needs
  the operator's login; the operator smoke-tests it post-merge.
- **Type consistency spot-checks:** `post_pass(card, state, sizing, cfg, *,
  digest, model, generated_at_ms, cost_usd_notional)` matches T10's call;
  `append_ledgers(final, cfg)` matches T11's call; `RecentFire` fields match
  T3's constructor; `LLMResponse` fields match T10's fake.
