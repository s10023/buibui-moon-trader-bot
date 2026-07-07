"""Nearest active structural zones (fvg / ob / eqh_eql / bos)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from analytics.brief.types import ZoneRow
from analytics.zones_lib import (
    extract_bos_zones,
    extract_eqh_eql_zones,
    extract_fvg_zones,
    extract_order_block_zones,
)


def _zone_bounds(zone: dict[str, Any]) -> tuple[float, float] | None:
    """(low, high) — eqh_eql/bos zones carry a single ``price`` line."""
    if "zone_low" in zone and "zone_high" in zone:
        return float(zone["zone_low"]), float(zone["zone_high"])
    if "price" in zone:
        p = float(zone["price"])
        return p, p
    return None


def build_zone_rows(
    frames_by_tf: dict[str, pd.DataFrame],
    ref_close: float,
    atr14: float,
    max_per_side: int,
) -> tuple[list[ZoneRow], list[ZoneRow]]:
    """(above, below) nearest ACTIVE zones, nearest-first, capped per tf/side.

    ``frames_by_tf`` maps tf -> COMPLETED-bars OHLCV. Distances are signed
    ``(nearest_edge - ref_close) / atr14`` in ATR14(1d) units; price inside a
    zone -> ``dist_atr=0.0`` + ``inside`` marker, listed on the below side
    (consistent with the levels rule ``dist <= 0`` -> below).
    """
    if atr14 <= 0:
        return [], []
    above: list[ZoneRow] = []
    below: list[ZoneRow] = []
    for tf, df in frames_by_tf.items():
        rows: list[ZoneRow] = []
        zones: list[dict[str, Any]] = []
        zones.extend(extract_fvg_zones(df))
        zones.extend(extract_order_block_zones(df))
        zones.extend(extract_eqh_eql_zones(df))
        zones.extend(extract_bos_zones(df))
        for zone in zones:
            if not zone.get("active", False):
                continue
            bounds = _zone_bounds(zone)
            if bounds is None:
                continue
            lo, hi = bounds
            if ref_close < lo:
                dist = (lo - ref_close) / atr14
                inside = False
            elif ref_close > hi:
                dist = (hi - ref_close) / atr14
                inside = False
            else:
                dist = 0.0
                inside = True
            rows.append(
                ZoneRow(
                    tf=tf,
                    zone_type=str(zone["zone_type"]),
                    direction=str(zone["direction"]),
                    zone_low=lo,
                    zone_high=hi,
                    dist_atr=dist,
                    inside=inside,
                )
            )
        tf_above = sorted((r for r in rows if r.dist_atr > 0), key=lambda r: r.dist_atr)
        tf_below = sorted(
            (r for r in rows if r.dist_atr <= 0), key=lambda r: -r.dist_atr
        )
        above.extend(tf_above[:max_per_side])
        below.extend(tf_below[:max_per_side])
    return above, below
