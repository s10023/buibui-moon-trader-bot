"""Human-readable formatters for backtest / combo / cross-TF results."""

import pandas as pd

from analytics.backtest.combo import ComboBacktestResult
from analytics.backtest.cross_tf import CrossTfComboBacktestResult
from analytics.backtest.engine import BacktestResult, Trade


def format_cross_tf_combo_table(
    results: "list[CrossTfComboBacktestResult]",
    min_trades: int = 3,
) -> str:
    """Format cross-TF combo results as a sorted table (avg_r descending)."""
    rows = [
        (
            f"{r.strategy_htf}@{r.tf_htf}+{r.strategy_ltf}@{r.tf_ltf}",
            r.result.symbol,
            r.window_hours,
            len(r.result.closed_trades),
            r.result.win_rate,
            r.result.avg_r,
            r.result.total_r,
        )
        for r in results
        if len(r.result.closed_trades) >= min_trades
    ]
    if not rows:
        return f"No cross-TF combos with >= {min_trades} trades."
    rows.sort(key=lambda x: x[5], reverse=True)
    header = f"{'Pair':<42} {'Symbol':<10} {'Win%':>5} {'W':>3} {'N':>4} {'AvgR':>6} {'TotR':>6}"
    lines = [header, "-" * len(header)]
    for pair, sym, _wh, n, wr, avg_r, tot_r in rows:
        lines.append(
            f"{pair:<42} {sym:<10} {wr:>4.0%} {int(wr * n):>3} {n:>4} {avg_r:>6.2f} {tot_r:>6.2f}"
        )
    return "\n".join(lines)


def format_combo_table(
    combo_results: list[ComboBacktestResult],
    min_trades: int = 3,
) -> str:
    """Format combo backtest results as a sorted table (avg_r descending)."""
    rows = [
        (
            f"{c.strategy_a}+{c.strategy_b}",
            c.result.symbol,
            c.result.timeframe,
            len(c.result.closed_trades),
            c.result.win_rate,
            c.result.avg_r,
            c.result.total_r,
            c.result.max_drawdown_r,
        )
        for c in combo_results
        if len(c.result.closed_trades) >= min_trades
    ]
    rows.sort(key=lambda r: r[5], reverse=True)  # sort by avg_r

    col = (32, 10, 5, 8, 8, 8, 9, 8)
    header = (
        f"  {'Combo':<{col[0]}}"
        f"{'Symbol':<{col[1]}}"
        f"{'TF':<{col[2]}}"
        f"{'Trades':>{col[3]}}"
        f"{'Win%':>{col[4]}}"
        f"{'AvgR':>{col[5]}}"
        f"{'TotalR':>{col[6]}}"
        f"{'MaxDD':>{col[7]}}"
    )
    sep = "  " + "─" * (sum(col) + 2)
    thick = "═" * (sum(col) + 4)
    lines = [
        f"\nStrategy Co-firing Results (±{combo_results[0].window if combo_results else 5} candles, min_trades={min_trades})",
        thick,
        header,
        sep,
    ]
    for combo, sym, tf, n, wr, avg_r, tot_r, dd in rows:
        lines.append(
            f"  {combo:<{col[0]}}"
            f"{sym:<{col[1]}}"
            f"{tf:<{col[2]}}"
            f"{n:>{col[3]}}"
            f"{wr:>{col[4] - 1}.1%} "
            f"{avg_r:>+{col[5]}.2f}R"
            f"{tot_r:>+{col[6]}.2f}R"
            f"{dd:>{col[7]}.2f}R"
        )
    if not rows:
        lines.append("  No combos met min_trades threshold.")
    lines.append(sep)
    return "\n".join(lines)


def format_result(result: BacktestResult) -> str:
    """Format a BacktestResult as a human-readable text summary."""
    fee_label = f"{result.fee_pct * 100:.4f}% taker" if result.fee_pct > 0.0 else "none"
    lines = [
        f"Backtest: {result.symbol} {result.timeframe} — {result.strategy}",
        "─" * 52,
        f"Signals:     {len(result.trades)} total, {len(result.closed_trades)} closed",
        f"Win rate:    {result.win_rate:.1%}  ({result.win_count}W / {result.loss_count}L)",
        f"Avg R:       {result.avg_r:+.2f}R",
        f"Total R:     {result.total_r:+.2f}R",
        f"Max DD:      -{result.max_drawdown_r:.2f}R",
        f"Fees:        {fee_label}",
    ]
    return "\n".join(lines)


def format_volume_split(results: list[BacktestResult]) -> str:
    """Show avg R split by low-volume / normal / spike trades, aggregated by strategy.

    Delta = normal_avg_r − low_vol_avg_r.
    Positive delta means normal-volume trades outperform → volume filter would help.
    Spike avg R shows whether high-conviction candles have additional edge.
    """
    from collections import defaultdict

    low_by_strat: dict[str, list[Trade]] = defaultdict(list)
    norm_by_strat: dict[str, list[Trade]] = defaultdict(list)
    spike_by_strat: dict[str, list[Trade]] = defaultdict(list)
    for r in results:
        low_by_strat[r.strategy].extend(r.low_vol_closed_trades)
        norm_by_strat[r.strategy].extend(r.normal_vol_closed_trades)
        spike_by_strat[r.strategy].extend(r.spike_vol_closed_trades)

    strategies = sorted(set(low_by_strat) | set(norm_by_strat) | set(spike_by_strat))

    def _avg(trades: list[Trade]) -> float | None:
        vals = [t.pnl_r for t in trades if t.pnl_r is not None]
        return sum(vals) / len(vals) if vals else None

    col = (22, 8, 7, 8, 7, 8, 7, 7)
    header = (
        f"  {'Strategy':<{col[0]}}"
        f"{'Low-vol':>{col[1]}}"
        f"{'Avg R':>{col[2]}}"
        f"  {'Normal':>{col[3]}}"
        f"{'Avg R':>{col[4]}}"
        f"  {'Spike':>{col[5]}}"
        f"{'Avg R':>{col[6]}}"
        f"  {'Delta':>{col[7]}}"
    )
    sep = "  " + "─" * (sum(col) + 6)
    thick = "═" * (sum(col) + 8)

    lines = [
        "\nVolume Impact (aggregated across all symbols × TFs)",
        thick,
        header,
        sep,
    ]

    for s in strategies:
        low = low_by_strat[s]
        norm = norm_by_strat[s]
        spike = spike_by_strat[s]
        low_r = _avg(low)
        norm_r = _avg(norm)
        spike_r = _avg(spike)
        delta = (norm_r - low_r) if (low_r is not None and norm_r is not None) else None
        low_r_s = f"{low_r:+.2f}R" if low_r is not None else "  n/a"
        norm_r_s = f"{norm_r:+.2f}R" if norm_r is not None else "  n/a"
        spike_r_s = f"{spike_r:+.2f}R" if spike_r is not None else "  n/a"
        delta_s = f"{delta:+.2f}R" if delta is not None else "  n/a"
        lines.append(
            f"  {s:<{col[0]}}"
            f"{len(low):>{col[1]}}"
            f"{low_r_s:>{col[2]}}"
            f"  {len(norm):>{col[3]}}"
            f"{norm_r_s:>{col[4]}}"
            f"  {len(spike):>{col[5]}}"
            f"{spike_r_s:>{col[6]}}"
            f"  {delta_s:>{col[7]}}"
        )

    lines.append(sep)
    lines.append(
        "  Delta = normal_avg_r − low_vol_avg_r  "
        "(positive = volume filter would help, negative = hurts)"
    )
    return "\n".join(lines)


def format_directional_volume_split(results: list[BacktestResult]) -> str:
    """Show avg R split by volume tier × direction (LONG / SHORT), aggregated by strategy.

    Use this to decide whether volume_suppress_long / volume_suppress_short
    are warranted. A large per-direction delta signals a directional suppress
    opportunity.
    """
    from collections import defaultdict

    long_low: dict[str, list[Trade]] = defaultdict(list)
    long_norm: dict[str, list[Trade]] = defaultdict(list)
    long_spike: dict[str, list[Trade]] = defaultdict(list)
    short_low: dict[str, list[Trade]] = defaultdict(list)
    short_norm: dict[str, list[Trade]] = defaultdict(list)
    short_spike: dict[str, list[Trade]] = defaultdict(list)

    for r in results:
        long_low[r.strategy].extend(r.long_low_vol_closed_trades)
        long_norm[r.strategy].extend(r.long_normal_vol_closed_trades)
        long_spike[r.strategy].extend(r.long_spike_vol_closed_trades)
        short_low[r.strategy].extend(r.short_low_vol_closed_trades)
        short_norm[r.strategy].extend(r.short_normal_vol_closed_trades)
        short_spike[r.strategy].extend(r.short_spike_vol_closed_trades)

    strategies = sorted(
        set(long_low)
        | set(long_norm)
        | set(long_spike)
        | set(short_low)
        | set(short_norm)
        | set(short_spike)
    )

    def _avg(trades: list[Trade]) -> float | None:
        vals = [t.pnl_r for t in trades if t.pnl_r is not None]
        return sum(vals) / len(vals) if vals else None

    def _r(v: float | None) -> str:
        return f"{v:+.2f}R" if v is not None else "  n/a"

    thick = "═" * 90
    lines = [
        "\nDirectional Volume Split (LONG / SHORT × Low / Normal / Spike avg R)",
        thick,
        f"  {'Strategy':<22}  {'Dir':>4}  {'Low-vol':>7}  {'n':>4}  "
        f"{'Normal':>7}  {'n':>4}  {'Spike':>7}  {'n':>4}  {'Δ(norm-low)':>11}",
        "  " + "─" * 87,
    ]

    for s in strategies:
        for label, low, norm, spike in [
            ("↑", long_low[s], long_norm[s], long_spike[s]),
            ("↓", short_low[s], short_norm[s], short_spike[s]),
        ]:
            low_r = _avg(low)
            norm_r = _avg(norm)
            spike_r = _avg(spike)
            delta = (
                (norm_r - low_r) if (low_r is not None and norm_r is not None) else None
            )
            lines.append(
                f"  {s:<22}  {label:>4}  {_r(low_r):>7}  {len(low):>4}  "
                f"{_r(norm_r):>7}  {len(norm):>4}  {_r(spike_r):>7}  {len(spike):>4}  "
                f"{_r(delta):>11}"
            )

    lines.append("  " + "─" * 87)
    lines.append(
        "  Δ(norm-low) > 0 → suppressing lows for that direction would help. "
        "Spike > norm → boost that direction."
    )
    return "\n".join(lines)


_TF_ORDER = {
    "1m": 0,
    "3m": 1,
    "5m": 2,
    "15m": 3,
    "30m": 4,
    "1h": 5,
    "2h": 6,
    "4h": 7,
    "1d": 8,
    "1w": 9,
}


def _tf_sort_key(tf: str) -> int:
    return _TF_ORDER.get(tf, 99)


def format_duration_table(results: list[BacktestResult]) -> str:
    """Show avg and median hold time per strategy × TF, aggregated across symbols."""
    from collections import defaultdict

    durations: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in results:
        durations[(r.strategy, r.timeframe)].extend(r.durations_h)

    keys = sorted(durations, key=lambda k: (k[0], _tf_sort_key(k[1])))

    def _median(vals: list[float]) -> float | None:
        s = sorted(vals)
        n = len(s)
        if not n:
            return None
        mid = n // 2
        return (s[mid - 1] + s[mid]) / 2 if n % 2 == 0 else s[mid]

    def _fmt(h: float | None) -> str:
        if h is None:
            return "  n/a"
        if h < 24:
            return f"{h:.1f}h"
        return f"{h / 24:.1f}d"

    col = (22, 6, 8, 8, 8, 8)
    header = (
        f"  {'Strategy':<{col[0]}}"
        f"{'TF':<{col[1]}}"
        f"{'Trades':>{col[2]}}"
        f"{'Avg':>{col[3]}}"
        f"{'Median':>{col[4]}}"
        f"{'Max':>{col[5]}}"
    )
    sep = "  " + "─" * (sum(col) + 2)
    thick = "═" * (sum(col) + 4)

    lines = ["\nTrade Duration (aggregated across symbols)", thick, header, sep]

    prev_strat = ""
    for strat, tf in keys:
        d = durations[(strat, tf)]
        if not d:
            continue
        avg = sum(d) / len(d)
        med = _median(d)
        label = strat if strat != prev_strat else ""
        prev_strat = strat
        lines.append(
            f"  {label:<{col[0]}}"
            f"{tf:<{col[1]}}"
            f"{len(d):>{col[2]}}"
            f"{_fmt(avg):>{col[3]}}"
            f"{_fmt(med):>{col[4]}}"
            f"{_fmt(max(d)):>{col[5]}}"
        )

    lines.append(sep)
    return "\n".join(lines)


def format_tp_sweep_table(
    results_by_tp: dict[float, list[BacktestResult]],
) -> str:
    """Show avg R per strategy × TF for each tp_r value, aggregated across symbols.

    results_by_tp: {tp_r_value: list[BacktestResult]}
    """
    tp_values = sorted(results_by_tp)

    keys: set[tuple[str, str]] = set()
    for results in results_by_tp.values():
        for r in results:
            keys.add((r.strategy, r.timeframe))
    sorted_keys = sorted(keys, key=lambda k: (k[0], _tf_sort_key(k[1])))

    def _collect(
        results: list[BacktestResult], strategy: str, tf: str
    ) -> tuple[float | None, float | None]:
        """Return (avg_r, win_rate) aggregated across all symbols for this strategy×TF."""
        trades: list[Trade] = []
        for r in results:
            if r.strategy == strategy and r.timeframe == tf:
                trades.extend(r.closed_trades)
        if not trades:
            return None, None
        r_vals = [t.pnl_r for t in trades if t.pnl_r is not None]
        avg = sum(r_vals) / len(r_vals) if r_vals else None
        wins = sum(1 for t in trades if t.outcome == "win")
        wr = wins / len(trades) if trades else None
        return avg, wr

    tp_col_w = 8
    name_col_w = 22
    tf_col_w = 6
    header = f"  {'Strategy':<{name_col_w}}{'TF':<{tf_col_w}}" + "".join(
        f"{tp_r:.1f}R".rjust(tp_col_w) for tp_r in tp_values
    )
    total_w = name_col_w + tf_col_w + tp_col_w * len(tp_values) + 2
    sep = "  " + "─" * total_w
    thick = "═" * (total_w + 2)

    lines = ["\nTP Ratio Comparison (aggregated across symbols)", thick, header, sep]

    prev_strat = ""
    for strat, tf in sorted_keys:
        label = strat if strat != prev_strat else ""
        prev_strat = strat
        row_r = f"  {label:<{name_col_w}}{tf:<{tf_col_w}}"
        row_w = f"  {'':>{name_col_w}}{'':>{tf_col_w}}"
        for tp in tp_values:
            avg, wr = _collect(results_by_tp[tp], strat, tf)
            cell_r = f"{avg:+.2f}R" if avg is not None else "  n/a"
            cell_w = f"{wr:.0%}" if wr is not None else ""
            row_r += f"{cell_r:>{tp_col_w}}"
            row_w += f"{cell_w:>{tp_col_w}}"
        lines.append(row_r)
        lines.append(row_w)

    lines.append(sep)
    lines.append("  Pick the tp_r column where avg R peaks per strategy × TF row.")
    lines.append(
        "  Watch win% — declining win% at high TP = lottery-ticket bias (discard)."
    )
    return "\n".join(lines)


def format_atr_sl_sweep_table(
    results_by_atr: dict[float, list[BacktestResult]],
) -> str:
    """Show avg R per strategy × TF for each atr_sl_multiplier value, aggregated across symbols.

    results_by_atr: {atr_sl_multiplier_value: list[BacktestResult]}
    """
    atr_values = sorted(results_by_atr)

    keys: set[tuple[str, str]] = set()
    for results in results_by_atr.values():
        for r in results:
            keys.add((r.strategy, r.timeframe))
    sorted_keys = sorted(keys, key=lambda k: (k[0], _tf_sort_key(k[1])))

    def _collect(
        results: list[BacktestResult], strategy: str, tf: str
    ) -> tuple[float | None, float | None]:
        trades: list[Trade] = []
        for r in results:
            if r.strategy == strategy and r.timeframe == tf:
                trades.extend(r.closed_trades)
        if not trades:
            return None, None
        r_vals = [t.pnl_r for t in trades if t.pnl_r is not None]
        avg = sum(r_vals) / len(r_vals) if r_vals else None
        wins = sum(1 for t in trades if t.outcome == "win")
        wr = wins / len(trades) if trades else None
        return avg, wr

    atr_col_w = 8
    name_col_w = 22
    tf_col_w = 6
    header = f"  {'Strategy':<{name_col_w}}{'TF':<{tf_col_w}}" + "".join(
        f"{v:.1f}×".rjust(atr_col_w) for v in atr_values
    )
    total_w = name_col_w + tf_col_w + atr_col_w * len(atr_values) + 2
    sep = "  " + "─" * total_w
    thick = "═" * (total_w + 2)

    lines = [
        "\nATR SL Multiplier Comparison (aggregated across symbols)",
        thick,
        header,
        sep,
    ]

    prev_strat = ""
    for strat, tf in sorted_keys:
        label = strat if strat != prev_strat else ""
        prev_strat = strat
        row_r = f"  {label:<{name_col_w}}{tf:<{tf_col_w}}"
        row_w = f"  {'':>{name_col_w}}{'':>{tf_col_w}}"
        for atr in atr_values:
            avg, wr = _collect(results_by_atr[atr], strat, tf)
            cell_r = f"{avg:+.2f}R" if avg is not None else "  n/a"
            cell_w = f"{wr:.0%}" if wr is not None else ""
            row_r += f"{cell_r:>{atr_col_w}}"
            row_w += f"{cell_w:>{atr_col_w}}"
        lines.append(row_r)
        lines.append(row_w)

    lines.append(sep)
    lines.append(
        "  Pick the multiplier column where avg R peaks per strategy × TF row."
    )
    return "\n".join(lines)


def format_seasonality(stats: pd.DataFrame) -> str:
    """Format a seasonality stats DataFrame as a human-readable text table."""
    if stats.empty:
        return "No seasonality data available."

    DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    header = f"  {'Value':<10} {'Avg Return':>12} {'Win Rate':>10} {'Count':>8}"
    sep = "  " + "─" * 44

    lines: list[str] = ["Seasonality Analysis", "═" * 52]

    for period_type in ["day_of_week", "hour_of_day", "week_of_month"]:
        subset = stats[stats["period_type"] == period_type].sort_values("period_value")
        if subset.empty:
            continue

        title = period_type.replace("_", " ").title()
        lines.append(f"\n{title}:")
        lines.append(header)
        lines.append(sep)

        for row in subset.to_dict("records"):
            val = int(row["period_value"])
            if period_type == "day_of_week":
                label = DAY_NAMES[val] if val < 7 else str(val)
            else:
                label = str(val)
            avg_ret = float(row["avg_return_pct"])
            win_rate = float(row["win_rate"])
            count = int(row["count"])
            lines.append(
                f"  {label:<10} {avg_ret:>+11.3f}% {win_rate:>9.1%} {count:>8}"
            )

    return "\n".join(lines)


def format_sweep_table(
    results: list[BacktestResult],
    min_trades: int = 20,
    min_trades_per_tf: dict[str, int] | None = None,
) -> str:
    """Format a ranked backtest sweep table as a string.

    Rows with fewer than the effective min_trades for their TF are excluded and counted
    in the footer. Per-TF thresholds in min_trades_per_tf take precedence over the
    global min_trades fallback. Results are sorted by avg_r descending.
    """

    def _eff_min(tf: str) -> int:
        return (
            min_trades_per_tf.get(tf, min_trades) if min_trades_per_tf else min_trades
        )

    qualifying = [r for r in results if len(r.closed_trades) >= _eff_min(r.timeframe)]
    hidden = len(results) - len(qualifying)

    qualifying.sort(key=lambda r: r.avg_r, reverse=True)

    col_w = (14, 6, 18, 8, 8, 8)
    header = (
        f"{'Symbol':<{col_w[0]}}"
        f"{'TF':<{col_w[1]}}"
        f"{'Strategy':<{col_w[2]}}"
        f"{'Win%':>{col_w[3]}}"
        f"{'Trades':>{col_w[4]}}"
        f"{'Avg R':>{col_w[5]}}"
    )
    sep = "─" * sum(col_w)
    thick_sep = "═" * sum(col_w)

    lines = [thick_sep, header, sep]

    if not qualifying:
        lines.append("  No results meet the minimum trade threshold.")
    else:
        for r in qualifying:
            win_pct = f"{r.win_rate * 100:.1f}%"
            avg_r = f"{r.avg_r:+.2f}R"
            lines.append(
                f"{r.symbol:<{col_w[0]}}"
                f"{r.timeframe:<{col_w[1]}}"
                f"{r.strategy:<{col_w[2]}}"
                f"{win_pct:>{col_w[3]}}"
                f"{len(r.closed_trades):>{col_w[4]}}"
                f"{avg_r:>{col_w[5]}}"
            )

    lines.append(sep)
    if hidden > 0:
        lines.append(f"  Hidden: {hidden} combo(s) with < {min_trades} trades")

    return "\n".join(lines)
