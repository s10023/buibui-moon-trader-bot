"""Volume-at-price profile from OHLCV — pure math, no DB/brief imports.

The M1 volume-profile primitive (spec
docs/superpowers/specs/2026-07-09-m1-indicator-state-design.md): each bar's
volume is spread over the price bins its [low, high] range overlaps,
proportional to overlap. Reusable outside the brief (F2, state-tag research).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class VolumeProfile:
    """Uniform price bins (``bin_edges`` ascending, len = len(volumes)+1)."""

    bin_edges: tuple[float, ...]
    volumes: tuple[float, ...]


def build_profile(hourly_df: pd.DataFrame, n_bins: int = 100) -> VolumeProfile | None:
    """Composite profile over the frame; None when it cannot be built.

    None cases: empty frame, zero price span (all bars at one price), or
    zero total volume. A zero-width bar (high == low) drops all its volume
    into the single bin containing that price (top edge -> last bin).
    """
    if hourly_df.empty:
        return None
    lows = hourly_df["low"].astype(float)
    highs = hourly_df["high"].astype(float)
    vols = hourly_df["volume"].astype(float)
    span_lo = float(lows.min())
    span_hi = float(highs.max())
    if span_hi <= span_lo or float(vols.sum()) <= 0.0:
        return None
    width = (span_hi - span_lo) / n_bins
    edges = [span_lo + i * width for i in range(n_bins + 1)]
    volumes = [0.0] * n_bins
    for low, high, vol in zip(lows, highs, vols, strict=True):
        if vol <= 0.0:
            continue
        if high <= low:
            idx = min(int((low - span_lo) / width), n_bins - 1)
            volumes[idx] += float(vol)
            continue
        bar_span = high - low
        first = max(0, min(int((low - span_lo) / width), n_bins - 1))
        last = max(0, min(int((high - span_lo) / width), n_bins - 1))
        for i in range(first, last + 1):
            overlap = min(high, edges[i + 1]) - max(low, edges[i])
            if overlap > 0:
                volumes[i] += float(vol) * (overlap / bar_span)
    return VolumeProfile(bin_edges=tuple(edges), volumes=tuple(volumes))


def value_area(profile: VolumeProfile, pct: float = 0.70) -> tuple[float, float, float]:
    """(poc, vah, val) — greedy expansion around the POC bin to ``pct``.

    Deterministic tie-breaks: equal-volume POC candidates -> lowest-price
    bin (first max); equal-volume neighbors during expansion -> upper bin.
    """
    vols = profile.volumes
    edges = profile.bin_edges
    total = sum(vols)
    poc_idx = max(range(len(vols)), key=lambda i: (vols[i], -i))
    lo = hi = poc_idx
    covered = vols[poc_idx]
    while covered < pct * total and (lo > 0 or hi < len(vols) - 1):
        up = vols[hi + 1] if hi < len(vols) - 1 else float("-inf")
        down = vols[lo - 1] if lo > 0 else float("-inf")
        if up >= down:  # tie prefers the upper bin
            hi += 1
            covered += vols[hi]
        else:
            lo -= 1
            covered += vols[lo]
    poc = (edges[poc_idx] + edges[poc_idx + 1]) / 2.0
    return poc, edges[hi + 1], edges[lo]
