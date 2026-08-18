"""Detector causality harness — a signal at bar `t` must not depend on bars after `t`.

Each detector is fed the series truncated at `t` and the signals it emits *at* `t`
are asserted byte-identical to the full-series run. A detector that reads future
bars produces a different answer under truncation, and is flagged.

Ported from the wifey fork's Phase 0.2 harness (their PR #71). The load-bearing
half is `test_harness_catches_injected_lookahead`: without a detector that is
*known* to peek, a green harness is indistinguishable from a harness with no
teeth — the failure mode this repo has already hit once on the xsmom causality
guard. Read that test as the proof, and the rest as the measurement.

Fixtures are the committed real-BTC OHLCV parquets in `tests/fixtures/`, so this
runs in CI rather than only by hand.

**Scope — a green run here does NOT mean "the book is causal".** The injected-peek
test proves the harness is REACHABLE, not that its coverage is complete:

- 19 detectors x {4h, 1d}. Timeframe is not a free variable — every detector in
  `DETECTOR_REGISTRY` takes `(df) -> df` with no `tf` argument, so a leak is a
  property of the algorithm and does not hide on an untested timeframe. 1h/15m
  are omitted for runtime only.
- Two cells emit nothing on the fixture and SKIP rather than pass:
  `cvd_divergence`/1d and `orb`/1d. A skip is untested, not clean.
- `_first_lookahead_violation` samples at most 25 signal times per cell. A cell
  with few signals is weakly tested, not strongly clean — `eqh_eql` emits ONE
  signal on the 1d fixture, so its 1d pass is worth nothing on its own (it was
  re-checked exhaustively on 4h at n=25 for this reason).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

from analytics.backtest.engine import run_backtest
from analytics.strategies._registry import DETECTOR_REGISTRY
from tests.conftest import _candle, _make_ohlcv

_FIXTURE_DIR = Path(__file__).parent / "fixtures"
_MAX_TRUNC_POINTS = 25
_FLOAT_DECIMALS = 8
# Production never runs a detector on less history than this: the live scanner
# slices `analytics.signal._common.scan_window(tf)` bars, which is 200 on every
# timeframe except 15m (600). Truncation points with less history than that sit
# inside a detector's own warmup guard — e.g. `liquidity_sweep` returns empty
# below `win + lookback` = 61 bars — and a refusal to run is NOT a lookahead.
# Points below this floor are reported as UNTESTED, never as passing.
_MIN_HISTORY_BARS = 200

Detector = Callable[[pd.DataFrame], pd.DataFrame]


def _load_fixture(tf: str) -> pd.DataFrame:
    """Load a committed BTCUSDT OHLCV fixture with a clean RangeIndex."""
    df = pd.read_parquet(_FIXTURE_DIR / f"btc_{tf}_200d.parquet")
    return df.reset_index(drop=True)


def _normalize_signals_at(
    sig: pd.DataFrame, t: int
) -> list[tuple[tuple[str, object], ...]]:
    """Order-independent, float-stable view of the signals whose open_time == t."""
    if sig.empty or "open_time" not in sig.columns:
        return []
    at = sig[sig["open_time"].astype("int64") == t]
    rows: list[tuple[tuple[str, object], ...]] = []
    for _, r in at.iterrows():
        cells: list[tuple[str, object]] = []
        for col in sorted(sig.columns):
            v = r[col]
            if isinstance(v, float):
                v = round(v, _FLOAT_DECIMALS)
            cells.append((col, v))
        rows.append(tuple(cells))
    return sorted(rows)


def _truncation_points(open_times: list[int]) -> list[int]:
    """Up to _MAX_TRUNC_POINTS evenly-spread points; always first + last."""
    uniq = sorted(set(open_times))
    if len(uniq) <= _MAX_TRUNC_POINTS:
        return uniq
    step = (len(uniq) - 1) / (_MAX_TRUNC_POINTS - 1)
    idxs = sorted({round(i * step) for i in range(_MAX_TRUNC_POINTS)})
    return [uniq[i] for i in idxs]


def _first_lookahead_violation(
    detector: Detector, df: pd.DataFrame
) -> tuple[int | None, int]:
    """Return (first violating open_time or None, number of points actually tested).

    The count is returned rather than inferred because a cell with zero tested
    points is UNTESTED, and an untested cell that returns None would otherwise be
    indistinguishable from a clean one — the failure mode this repo files as a
    powered null.
    """
    full = detector(df)
    if full.empty:
        return None, 0
    times = df["open_time"].astype("int64").to_numpy()
    testable = [
        t
        for t in full["open_time"].astype("int64").tolist()
        if int((times <= t).sum()) >= _MIN_HISTORY_BARS
    ]
    if not testable:
        return None, 0
    tested = 0
    for t in _truncation_points(testable):
        trunc = df[df["open_time"] <= t].reset_index(drop=True)
        tested += 1
        if _normalize_signals_at(detector(trunc), t) != _normalize_signals_at(full, t):
            return t, tested
    return None, tested


def test_harness_catches_injected_lookahead() -> None:
    """A detector that reads the NEXT bar must be flagged — proves the harness bites."""
    df = _load_fixture("1d")

    def peeking(d: pd.DataFrame) -> pd.DataFrame:
        # Emit a long signal at every bar i<n-1 with sl_price taken from the
        # FUTURE bar's close — a textbook lookahead.
        n = len(d)
        ot = d["open_time"].to_numpy()
        cl = d["close"].to_numpy(dtype=float)
        rows = [
            {
                "open_time": int(ot[i]),
                "direction": "long",
                "reason": "peek",
                "sl_price": float(cl[i + 1]),
            }
            for i in range(n - 1)
        ]
        return pd.DataFrame(
            rows, columns=["open_time", "direction", "reason", "sl_price"]
        )

    violation, tested = _first_lookahead_violation(peeking, df)
    assert tested > 0, "meta-test is vacuous if nothing was tested"
    assert violation is not None


def test_harness_passes_a_causal_detector() -> None:
    """A detector reading only bar i must NOT be flagged — proves it is not trivially red."""
    df = _load_fixture("1d")

    def causal(d: pd.DataFrame) -> pd.DataFrame:
        ot = d["open_time"].to_numpy()
        op = d["open"].to_numpy(dtype=float)
        cl = d["close"].to_numpy(dtype=float)
        rows = [
            {
                "open_time": int(ot[i]),
                "direction": "long",
                "reason": "causal",
                "sl_price": float(min(op[i], cl[i])),
            }
            for i in range(len(d))
            if cl[i] > op[i]
        ]
        return pd.DataFrame(
            rows, columns=["open_time", "direction", "reason", "sl_price"]
        )

    violation, tested = _first_lookahead_violation(causal, df)
    assert tested > 0, "meta-test is vacuous if nothing was tested"
    assert violation is None


# Detectors with a confirmed, tracked lookahead. xfail(strict) so the follow-up
# fix PR is FORCED to remove the entry when it makes the cell causal — a
# non-strict xfail would let a fix land while silently still leaking.
# Measured 2026-08-18 on the committed BTC fixtures, then FIXED in the same
# branch. Recorded because the numbers are the reason the fix exists:
#
#   before   bos              4h  60/60 sampled violate (100%)   1d  17/17 (100%)
#            liquidity_sweep  4h  14/60 sampled violate (23.3%)  1d   8/19 (42.1%)
#   after    both             0 violations at either timeframe
#
# `bos` was exactly 100% because it stamped every signal at the SWING bar,
# whose `center=True` window of `2*swing_lookback+1` reads `i+1...i+5` — so no
# bos signal was ever causal. It now stamps at the confirmation bar.
#
# `liquidity_sweep` referenced pivots from the same centred window and so leaked
# only when the pivot was recent enough for its window to extend past the signal
# bar; its lookups are now bounded to pivots confirmed as of the signal bar. Its
# old comment called this "acceptable lookahead ... consistent with
# detect_eqh_eql", and the consistency half was false as measured: `eqh_eql`
# violated 0 of 26 tested cells despite a near-identical rolling construction.
#
# Kept as an empty set rather than deleted: this is where a newly-found leak is
# parked, and xfail(strict) forces the fixing PR to empty it again.
_KNOWN_LOOKAHEAD_DETECTORS: set[str] = set()


@pytest.mark.parametrize("tf", ["4h", "1d"])
@pytest.mark.parametrize("detector_name", sorted(DETECTOR_REGISTRY))
def test_detector_has_no_lookahead(
    detector_name: str, tf: str, request: pytest.FixtureRequest
) -> None:
    if detector_name in _KNOWN_LOOKAHEAD_DETECTORS:
        request.node.add_marker(
            pytest.mark.xfail(
                strict=True,
                reason=(
                    f"{detector_name} reads future bars (centred swing window). "
                    "Measured, tracked, and NOT yet fixed — fixing it moves the "
                    "regression goldens, which is a decision rather than a step."
                ),
            )
        )
    detector = DETECTOR_REGISTRY[detector_name]
    df = _load_fixture(tf)
    if detector(df).empty:
        pytest.skip(f"{detector_name} emits no signals on the {tf} fixture")
    violation, tested = _first_lookahead_violation(detector, df)
    if tested == 0:
        pytest.skip(
            f"{detector_name} on {tf}: no signal has {_MIN_HISTORY_BARS}+ bars of "
            "history on this fixture — untested, not clean"
        )
    assert violation is None, (
        f"{detector_name} on {tf}: signals at open_time={violation} differ between "
        f"the full-series and truncated-at-t runs — the detector reads future bars."
    )


# --- Backtest entry-path causality ------------------------------------------

_T = 1_700_000_000_000


def _long_signal(open_time: int) -> pd.DataFrame:
    return pd.DataFrame(
        [{"open_time": open_time, "direction": "long", "reason": "test"}],
        columns=["open_time", "direction", "reason"],
    )


def test_backtest_entry_is_strictly_next_bar_open() -> None:
    """Entry fills at the NEXT bar's open — never the signal bar's close, never a future bar."""
    ohlcv = _make_ohlcv(
        [
            _candle(_T + 0, 100, 105, 95, 102),  # idx 0: signal candle (close=102)
            _candle(_T + 1, 100, 103, 99, 101),  # idx 1: entry candle (open=100)
            _candle(_T + 2, 101, 106, 99, 105),  # idx 2: resolves
        ]
    )
    res = run_backtest(
        ohlcv, _long_signal(_T + 0), "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0
    )
    assert len(res.trades) == 1
    tr = res.trades[0]
    assert tr.signal_time == _T + 0
    assert tr.entry_time == _T + 1  # strictly the next bar
    # that bar's OPEN, not the signal candle's close (102)
    assert tr.entry_price == pytest.approx(100.0)


def test_backtest_entry_independent_of_future_bars() -> None:
    """Truncating the series right after the entry bar leaves entry price/time unchanged."""
    candles = [
        _candle(_T + 0, 100, 105, 95, 102),  # signal
        _candle(_T + 1, 100, 103, 99, 101),  # entry (no SL/TP hit here)
        _candle(_T + 2, 101, 106, 99, 105),  # would resolve the trade in the full run
        _candle(_T + 3, 105, 130, 104, 129),  # large future move
    ]
    sig = _long_signal(_T + 0)
    full = run_backtest(
        _make_ohlcv(candles), sig, "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0
    )
    trunc = run_backtest(
        _make_ohlcv(candles[:2]), sig, "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0
    )
    assert len(full.trades) == 1 and len(trunc.trades) == 1
    assert trunc.trades[0].entry_time == full.trades[0].entry_time == _T + 1
    assert trunc.trades[0].entry_price == pytest.approx(full.trades[0].entry_price)
    assert trunc.trades[0].entry_price == pytest.approx(100.0)
