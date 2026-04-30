"""Live co-fire confluence detection — same-TF and cross-TF."""

from typing import Any

import duckdb
import pandas as pd

from analytics.data_store import get_signals_history
from analytics.indicators_lib import STRATEGY_REGISTRY
from analytics.signal._common import parse_timeframe_secs
from analytics.signal.types import ConfluenceData, SignalEvent


def _find_live_cofire(
    events: list[SignalEvent],
    ohlcv: pd.DataFrame,
    conn: duckdb.DuckDBPyConnection,
    combo_lookup: "dict[tuple[str, str, frozenset[str]], Any]",
    symbol: str,
    tf: str,
    window: int,
    min_avg_r: float,
) -> "ConfluenceData | None":
    """Return ConfluenceData for the best co-firing pair, or None.

    Checks two signal sources in order:
    1. Same-cycle events (candles_ago=0): two strategies fired this candle.
    2. Cross-cycle: recent signals stored in the DB for the past `window` candles.

    Returns the pair with the highest backtest avg_r that meets min_avg_r.
    Design note: keyed by (symbol, tf, frozenset) so cross-TF extension (step 4)
    can query a different tf key without restructuring this function.
    """
    if not events or not combo_lookup:
        return None

    # Build candle-index map for O(1) candles_ago computation.
    times: list[int] = ohlcv["open_time"].astype("int64").tolist()
    time_to_idx: dict[int, int] = {t: i for i, t in enumerate(times)}

    current_open_time = int(events[0].open_time)
    current_idx = time_to_idx.get(current_open_time, len(times) - 1)
    current_direction = events[0].direction
    current_strats = {e.strategy for e in events}

    best_r: float = -1.0
    best: ConfluenceData | None = None

    def _consider(primary_strat: str, co_strat: str, co_open_time: int) -> None:
        nonlocal best_r, best
        key: tuple[str, str, frozenset[str]] = (
            symbol,
            tf,
            frozenset({primary_strat, co_strat}),
        )
        row = combo_lookup.get(key)
        if row is None or row["avg_r"] < min_avg_r:
            return
        co_idx = time_to_idx.get(co_open_time, -1)
        if co_idx < 0:
            return
        candles_ago = current_idx - co_idx
        if candles_ago < 0 or candles_ago > window:
            return
        if row["avg_r"] <= best_r:
            return
        co_spec = STRATEGY_REGISTRY.get(co_strat)
        primary_spec = STRATEGY_REGISTRY.get(primary_strat)
        best_r = row["avg_r"]
        best = ConfluenceData(
            co_strategy=co_strat,
            candles_ago=candles_ago,
            avg_r=row["avg_r"],
            trades=int(row["closed_trades"]),
            win_rate=float(row["win_rate"]),
            type_a=co_spec.strategy_type if co_spec else "",
            type_b=primary_spec.strategy_type if primary_spec else "",
        )

    # 1. Same-cycle: pairs within dir_events (all at current_open_time).
    strat_list = list(current_strats)
    for i, sa in enumerate(strat_list):
        for sb in strat_list[i + 1 :]:
            _consider(sa, sb, current_open_time)

    # 2. Cross-cycle: query DB signals within the window.
    candle_ms = parse_timeframe_secs(tf) * 1000
    window_start_ms = current_open_time - window * candle_ms
    # Exclude the current candle (already covered by same-cycle check above).
    window_end_ms = current_open_time - candle_ms
    if window_end_ms >= window_start_ms:
        try:
            hist = get_signals_history(conn, symbol, tf, window_start_ms, window_end_ms)
        except Exception:
            hist = pd.DataFrame()
        if not hist.empty:
            for _, db_row in hist.iterrows():
                hist_strategy = str(db_row["strategy"])
                if str(db_row["direction"]) != current_direction:
                    continue
                hist_open_time = int(db_row["open_time"])
                for current_strat in current_strats:
                    if hist_strategy == current_strat:
                        continue
                    _consider(current_strat, hist_strategy, hist_open_time)

    return best


def _parse_htf_ltf_pairs(
    cross_tf_pairs: list[str],
) -> list[tuple[str, str]]:
    """Parse ["4h:15m", "4h:1h"] → [("4h", "15m"), ("4h", "1h")]."""
    result: list[tuple[str, str]] = []
    for entry in cross_tf_pairs:
        parts = entry.split(":")
        if len(parts) == 2:
            result.append((parts[0].strip(), parts[1].strip()))
    return result


def _find_cross_tf_cofire(
    events: list["SignalEvent"],
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    tf_ltf: str,
    cross_tf_lookup: "dict[tuple[str, str, str, str, str], Any]",
    cross_tf_pairs: list[tuple[str, str]],
    window_hours: float,
    min_avg_r: float,
) -> "ConfluenceData | None":
    """Return ConfluenceData for the best cross-TF co-firing pair, or None.

    For each (tf_htf, tf_ltf) pair where tf_ltf matches the current TF:
    1. Query the signals DB for recent HTF signals in the same direction.
    2. Look up (symbol, tf_htf, tf_ltf, strategy_htf, strategy_ltf) in the lookup.
    3. Return the best match (highest avg_r ≥ min_avg_r).

    candles_ago is expressed in LTF candles for display consistency with same-TF.
    """
    from analytics.indicators_lib import STRATEGY_REGISTRY

    if not events or not cross_tf_lookup:
        return None

    current_open_time = int(events[0].open_time)
    current_direction = events[0].direction
    current_strats = {e.strategy for e in events}

    window_ms = int(window_hours * 3600 * 1000)
    ltf_candle_ms = parse_timeframe_secs(tf_ltf) * 1000

    best_r: float = -1.0
    best: ConfluenceData | None = None

    # Only check pairs where LTF matches current TF.
    relevant_htfs = [htf for htf, ltf in cross_tf_pairs if ltf == tf_ltf]
    if not relevant_htfs:
        return None

    for tf_htf in relevant_htfs:
        window_start_ms = current_open_time - window_ms
        try:
            hist = get_signals_history(
                conn, symbol, tf_htf, window_start_ms, current_open_time
            )
        except Exception:
            continue
        if hist.empty:
            continue

        for _, db_row in hist.iterrows():
            htf_strat = str(db_row["strategy"])
            if str(db_row["direction"]) != current_direction:
                continue
            htf_open_time = int(db_row["open_time"])

            for ltf_strat in current_strats:
                key: tuple[str, str, str, str, str] = (
                    symbol,
                    tf_htf,
                    tf_ltf,
                    htf_strat,
                    ltf_strat,
                )
                row = cross_tf_lookup.get(key)
                if row is None or row["avg_r"] < min_avg_r:
                    continue
                if row["avg_r"] <= best_r:
                    continue

                # Express candles_ago in LTF candles.
                elapsed_ms = current_open_time - htf_open_time
                candles_ago = max(0, int(elapsed_ms / ltf_candle_ms))

                htf_spec = STRATEGY_REGISTRY.get(htf_strat)
                ltf_spec = STRATEGY_REGISTRY.get(ltf_strat)
                best_r = row["avg_r"]
                best = ConfluenceData(
                    co_strategy=htf_strat,
                    candles_ago=candles_ago,
                    avg_r=row["avg_r"],
                    trades=int(row["closed_trades"]),
                    win_rate=float(row["win_rate"]),
                    type_a=htf_spec.strategy_type if htf_spec else "",
                    type_b=ltf_spec.strategy_type if ltf_spec else "",
                    htf_tf=tf_htf,
                    ltf_tf=tf_ltf,
                )

    return best
