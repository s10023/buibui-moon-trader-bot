"""Overlapping-position paper book — the causal forward pass (P1 spec §4).

Processes resolved ledger trades in entry-time order, applying concurrent +
cluster risk caps in real time and marking open positions to a daily MTM
equity curve on TWO bases in parallel:

  - fixed-notional (risk-% of the initial `capital`) — the headline curve.
  - compounding (risk-% of current equity) — what the vol-governor reads.

Same-day-resolving trades bank realized R on their exit day (no prior mark).
Pure: no DB / network / clock. Marking uses caller-supplied 1d close series
aligned to `daily_index`. The vol governor + regime modulator are wired in by
`PaperBook._g_vol` / `regime_by_signal`; this file's defaults are neutral so
the caps/marking mechanics test in isolation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from portfolio.sizing import (
    SizingConfig,
    apply_caps,
    cluster_of,
    effective_risk_fraction,
    regime_multiplier,
    risk_per_unit,
    vol_governor,
)


@dataclass(frozen=True)
class LedgerTrade:
    signal_id: str
    symbol: str
    tf: str
    strategy: str
    direction: str
    entry_ts_ms: int
    exit_ts_ms: int
    entry_price: float
    sl_price: float
    outcome: str
    realized_r: float


@dataclass(frozen=True)
class SizedTrade:
    signal_id: str
    symbol: str
    tf: str
    strategy: str
    direction: str
    entry_idx: int
    exit_idx: int
    r_eff: float
    g_vol: float
    g_regime: float
    rc_fixed: float
    rc_comp: float
    pnl_fixed: float
    pnl_comp: float
    realized_r: float
    regime: str | None


@dataclass(frozen=True)
class BookResult:
    daily_index: np.ndarray
    capital: float
    pnl_fixed: np.ndarray
    pnl_comp: np.ndarray
    sized: list[SizedTrade]
    skipped: list[tuple[str, str]]


class PaperBook:
    """Replays sized trades onto dual-basis daily MTM curves with live caps."""

    def __init__(
        self,
        cfg: SizingConfig,
        daily_index: np.ndarray,
        close_by_symbol: dict[str, np.ndarray],
        regime_by_signal: dict[str, str] | None = None,
    ) -> None:
        self.cfg = cfg
        self.daily_index = daily_index
        self.close_by_symbol = close_by_symbol
        self.regime_by_signal = regime_by_signal

    def _g_vol(self, pnl_comp: np.ndarray, entry_idx: int) -> float:
        """Trailing realized vol of the compounding curve, days < entry_idx."""
        lo = max(0, entry_idx - self.cfg.vol_window_days)
        if entry_idx - lo < 2:
            return 1.0
        equity = self.cfg.capital + pnl_comp[lo:entry_idx]
        if np.any(equity[:-1] <= 0.0):
            return 1.0
        rets = np.diff(equity) / equity[:-1]
        if rets.size < 2:
            return 1.0
        sd = float(np.std(rets, ddof=1))
        realized_vol = sd * math.sqrt(self.cfg.annualization_days)
        return vol_governor(realized_vol, self.cfg)

    def run(self, trades: list[LedgerTrade]) -> BookResult:
        n = len(self.daily_index)
        pnl_fixed = np.zeros(n)
        pnl_comp = np.zeros(n)
        open_positions: list[
            tuple[int, float, str]
        ] = []  # (exit_ts_ms, r_eff, cluster)
        sized: list[SizedTrade] = []
        skipped: list[tuple[str, str]] = []

        for t in sorted(trades, key=lambda x: x.entry_ts_ms):
            entry_idx = (
                int(np.searchsorted(self.daily_index, t.entry_ts_ms, side="right")) - 1
            )
            if entry_idx < 0:
                skipped.append((t.signal_id, "before_grid"))
                continue
            exit_idx = (
                int(np.searchsorted(self.daily_index, t.exit_ts_ms, side="right")) - 1
            )
            exit_idx = max(exit_idx, entry_idx)

            rpu = risk_per_unit(t.entry_price, t.sl_price)
            if rpu <= 0.0:
                skipped.append((t.signal_id, "zero_risk"))
                continue

            open_positions = [p for p in open_positions if p[0] > t.entry_ts_ms]

            g_vol = self._g_vol(pnl_comp, entry_idx)
            regime = (
                None
                if self.regime_by_signal is None
                else self.regime_by_signal.get(t.signal_id)
            )
            g_regime = regime_multiplier(regime, self.cfg)
            r_eff_candidate = effective_risk_fraction(
                self.cfg, g_vol=g_vol, g_regime=g_regime
            )

            cluster = cluster_of(t.symbol, self.cfg)
            open_total = sum(p[1] for p in open_positions)
            open_cluster = sum(p[1] for p in open_positions if p[2] == cluster)
            r_eff = apply_caps(
                r_eff_candidate,
                symbol=t.symbol,
                open_risk_total=open_total,
                open_risk_cluster=open_cluster,
                cfg=self.cfg,
            )
            if r_eff <= 0.0:
                skipped.append((t.signal_id, "cap_breach"))
                continue

            comp_equity_at_entry = self.cfg.capital + pnl_comp[entry_idx]
            rc_fixed = r_eff * self.cfg.capital
            rc_comp = r_eff * comp_equity_at_entry
            side = 1.0 if t.direction == "long" else -1.0
            closes = self.close_by_symbol.get(t.symbol)

            for arr, rc in ((pnl_fixed, rc_fixed), (pnl_comp, rc_comp)):
                if exit_idx > entry_idx and closes is not None:
                    seg = closes[entry_idx:exit_idx]
                    unreal = rc * side * (seg - t.entry_price) / rpu
                    arr[entry_idx:exit_idx] += np.nan_to_num(unreal)
                arr[exit_idx:] += rc * t.realized_r

            open_positions.append((t.exit_ts_ms, r_eff, cluster))
            sized.append(
                SizedTrade(
                    signal_id=t.signal_id,
                    symbol=t.symbol,
                    tf=t.tf,
                    strategy=t.strategy,
                    direction=t.direction,
                    entry_idx=entry_idx,
                    exit_idx=exit_idx,
                    r_eff=r_eff,
                    g_vol=g_vol,
                    g_regime=g_regime,
                    rc_fixed=rc_fixed,
                    rc_comp=rc_comp,
                    pnl_fixed=rc_fixed * t.realized_r,
                    pnl_comp=rc_comp * t.realized_r,
                    realized_r=t.realized_r,
                    regime=regime,
                )
            )

        return BookResult(
            daily_index=self.daily_index,
            capital=self.cfg.capital,
            pnl_fixed=pnl_fixed,
            pnl_comp=pnl_comp,
            sized=sized,
            skipped=skipped,
        )
