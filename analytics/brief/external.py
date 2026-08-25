"""External-context reader (M3): verified heatmap/liq-map snapshots.

Pure and deterministic: validates + loads operator-verified JSON snapshots
(written by /ingest-charts via tools/chart_drops.py) and derives the
per-symbol External block against the brief's ref price. Disk is untrusted
(hand-editable): validation failures become notes, never exceptions.
Conn-free; the bundle passes everything in. The validator is shared with
the writer side (tools/chart_drops.py imports it) so writer and reader
hold one contract.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from analytics.brief.types import ExternalClusterRow, ExternalSnapshot, ExternalState

SCHEMA_VERSION = "external-levels-v1"
ALLOWED_PANELS = ("liq_heatmap", "book_heatmap", "liq_map")
ALLOWED_KINDS = ("liq", "book")
ALLOWED_INTENSITIES = ("high", "med", "low")
ALLOWED_SCOPES = ("pair", "agg")
ALLOWED_SPOT_SOURCES = ("printed", "axis")
_SPOT_DEVIATION_FRAC = 0.10
_MS_PER_HOUR = 3_600_000

# ST74. `window` is dimension 5 of the dedup key below, so it decides whether two
# captures SUPERSEDE each other or coexist. Vision reads it off the chart as free text and
# returns "1 day" where another panel of the same thing returns "1d" -- two keys, two
# surviving snapshots, both presented as if they were different windows.
#
# ⚠ Deliberately CONSERVATIVE: this collapses FORMAT variants of one value, never unit
# EQUIVALENCES. "24h" and "1d" name the same span and are left distinct, because the
# capture set really does grab both and merging them would silently drop one panel's read
# -- the failure this fixes, in the opposite direction. Anything unrecognised passes
# through case- and whitespace-folded, so it still dedups against itself and never merges
# with something else.
_WINDOW_RE = re.compile(r"^(?P<n>\d+)\s*(?P<unit>[a-z]+)$")
_WINDOW_UNITS = {
    "m": "m",
    "min": "m",
    "mins": "m",
    "minute": "m",
    "minutes": "m",
    "h": "h",
    "hr": "h",
    "hrs": "h",
    "hour": "h",
    "hours": "h",
    "d": "d",
    "day": "d",
    "days": "d",
    "w": "w",
    "wk": "w",
    "wks": "w",
    "week": "w",
    "weeks": "w",
    "mo": "mo",
    "mon": "mo",
    "month": "mo",
    "months": "mo",
    "y": "y",
    "yr": "y",
    "yrs": "y",
    "year": "y",
    "years": "y",
}


def normalize_window(window: object) -> str:
    """Canonical dedup token for a free-text `window`. Never raises.

    Bare "m" stays "m" and is NOT resolved to minutes or months: the two are genuinely
    ambiguous in this corpus and guessing would merge unrelated snapshots, which is the
    class of bug this function exists to prevent rather than to trade sideways.
    """
    if window is None:
        return ""
    folded = " ".join(str(window).strip().lower().split())
    if not folded:
        return ""
    match = _WINDOW_RE.match(folded)
    if match is None:
        return folded
    unit = _WINDOW_UNITS.get(match.group("unit"))
    if unit is None:
        return folded
    return f"{int(match.group('n'))}{unit}"


_REQUIRED_KEYS = {
    "schema",
    "source",
    "symbol",
    "panel",
    "window",
    "scope",
    "captured_at_ms",
    "ingested_at_ms",
    "verified",
    "spot_price_hint",
    "clusters",
    "notes",
}
_CLUSTER_KEYS = {"price_lo", "price_hi", "kind", "intensity", "label"}
# Additive, absent == null == unspecified. `venue` 2026-07-16; `spot_source`
# 2026-08-07 -- the `/ingest-charts` extractor had been asked for `spot_source`
# all along (SKILL.md step 2) while this validator rejected unknown top-level
# keys outright, so the field had nowhere to land and was silently dropped at
# the write step. It records HOW the extractor read spot: "printed" off a label
# on the chart, or "axis" inferred from the price scale. That is provenance a
# trust guard can use, and it is free -- vision already computed it, so
# discarding it would mean re-running vision to recover it.
_OPTIONAL_KEYS = {"venue", "spot_source"}


def _is_num(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_cluster(idx: int, item: object) -> list[str]:
    if not isinstance(item, dict):
        return [f"clusters[{idx}]: not an object"]
    keys = set(item)
    if keys != _CLUSTER_KEYS:
        bad = sorted(keys.symmetric_difference(_CLUSTER_KEYS))
        return [f"clusters[{idx}]: bad keys {bad}"]
    problems: list[str] = []
    if not _is_num(item["price_lo"]) or not _is_num(item["price_hi"]):
        problems.append(f"clusters[{idx}]: prices not numbers")
    elif float(item["price_lo"]) > float(item["price_hi"]):
        problems.append(f"clusters[{idx}]: price_lo > price_hi")
    elif float(item["price_lo"]) <= 0:
        problems.append(f"clusters[{idx}]: price_lo <= 0")
    if item["kind"] not in ALLOWED_KINDS:
        problems.append(f"clusters[{idx}]: kind {item['kind']!r} invalid")
    if item["intensity"] not in ALLOWED_INTENSITIES:
        problems.append(f"clusters[{idx}]: intensity {item['intensity']!r} invalid")
    if not isinstance(item["label"], str):
        problems.append(f"clusters[{idx}]: label not a string")
    return problems


def validate_snapshot_dict(data: object) -> list[str]:
    """Contract check shared by writer and reader. [] means valid.

    Unknown top-level keys are rejected — the schema version bumps instead.
    ``venue`` is the one sanctioned optional key (schema stays
    ``external-levels-v1``): absent or null both mean unspecified.
    """
    if not isinstance(data, dict):
        return ["snapshot is not a JSON object"]
    problems: list[str] = []
    keys = set(data)
    missing = _REQUIRED_KEYS - keys
    unknown = keys - _REQUIRED_KEYS - _OPTIONAL_KEYS
    if missing:
        problems.append(f"missing keys: {sorted(missing)}")
    if unknown:
        problems.append(f"unknown keys: {sorted(unknown)}")
    if problems:
        return problems
    if data["schema"] != SCHEMA_VERSION:
        problems.append(f"schema {data['schema']!r} != {SCHEMA_VERSION!r}")
    for key in ("source", "symbol"):
        if not isinstance(data[key], str) or not data[key]:
            problems.append(f"{key}: not a non-empty string")
    if not isinstance(data["notes"], str):
        problems.append("notes: not a string")
    if data["panel"] not in ALLOWED_PANELS:
        problems.append(f"panel {data['panel']!r} not in {ALLOWED_PANELS}")
    if data["window"] is not None and not isinstance(data["window"], str):
        problems.append("window: not a string or null")
    if data["scope"] is not None and data["scope"] not in ALLOWED_SCOPES:
        problems.append(f"scope {data['scope']!r} not in {ALLOWED_SCOPES}")
    venue = data.get("venue")
    if venue is not None and (not isinstance(venue, str) or not venue):
        problems.append("venue: not a non-empty string or null")
    spot_source = data.get("spot_source")
    if spot_source is not None and spot_source not in ALLOWED_SPOT_SOURCES:
        problems.append(f"spot_source {spot_source!r} not in {ALLOWED_SPOT_SOURCES}")
    for key in ("captured_at_ms", "ingested_at_ms"):
        value = data[key]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            problems.append(f"{key}: not a positive integer")
    if data["verified"] is not True:
        problems.append("verified: must be true")
    if data["spot_price_hint"] is not None and not _is_num(data["spot_price_hint"]):
        problems.append("spot_price_hint: not a number or null")
    if not isinstance(data["clusters"], list) or not data["clusters"]:
        problems.append("clusters: not a non-empty list")
    else:
        for idx, item in enumerate(data["clusters"]):
            problems.extend(_check_cluster(idx, item))
    return problems


def _build_snapshot(
    data: dict[str, Any],
    ref_close: float,
    atr14: float,
    as_of_ms: int,
    max_rows_per_side: int,
    notes: list[str],
) -> ExternalSnapshot:
    above: list[ExternalClusterRow] = []
    below: list[ExternalClusterRow] = []
    for item in data["clusters"]:
        lo, hi = float(item["price_lo"]), float(item["price_hi"])
        mid = (lo + hi) / 2.0
        row = ExternalClusterRow(
            price_lo=lo,
            price_hi=hi,
            kind=str(item["kind"]),
            intensity=str(item["intensity"]),
            label=str(item["label"]),
            dist_atr=(mid - ref_close) / atr14,
        )
        (above if mid >= ref_close else below).append(row)
    above.sort(key=lambda r: r.dist_atr)  # nearest first
    below.sort(key=lambda r: -r.dist_atr)  # nearest first (least negative)
    hint = data["spot_price_hint"]
    deviation = (
        hint is not None
        and ref_close > 0
        and abs(float(hint) - ref_close) / ref_close > _SPOT_DEVIATION_FRAC
    )
    if deviation:
        notes.append(
            f"external: spot hint deviates ({data['source']} {data['panel']})"
            " — check symbol/axis read"
        )
    return ExternalSnapshot(
        source=str(data["source"]),
        venue=None if data.get("venue") is None else str(data["venue"]),
        panel=str(data["panel"]),
        window=data["window"],
        scope=data["scope"],
        captured_at_ms=int(data["captured_at_ms"]),
        age_hours=(as_of_ms - int(data["captured_at_ms"])) / _MS_PER_HOUR,
        spot_price_hint=None if hint is None else float(hint),
        spot_hint_deviation=bool(deviation),
        clusters_above=above[:max_rows_per_side],
        clusters_below=below[:max_rows_per_side],
    )


def load_external_state(
    dir_path: Path,
    symbol: str,
    ref_close: float,
    atr14: float,
    as_of_ms: int,
    allowed_sources: tuple[str, ...],
    max_age_hours: float,
    max_rows_per_side: int,
) -> tuple[ExternalState | None, list[str]]:
    """(state, notes) for one symbol. Notes are UNPREFIXED (bundle adds it).

    Latest fresh snapshot per (source, venue, scope, panel, normalized window) —
    Binance-pair vs exchange-aggregated vs Hyperliquid snapshots of the
    same panel+window coexist rather than clobbering each other; absent
    dir or no files for this symbol -> (None, []) silently (feature is
    opt-in by usage); all-stale -> the re-drop note; malformed/unknown-source
    files -> per-file notes, never exceptions.
    """
    if not dir_path.is_dir():
        return None, []
    notes: list[str] = []
    fresh: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    stale_latest_ms: int | None = None
    for path in sorted(dir_path.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            # ValueError covers json.JSONDecodeError AND UnicodeDecodeError
            # (bad encoding from read_text) — notes, never exceptions.
            notes.append(f"external: unreadable {path.name} ({exc})")
            continue
        claimed = data.get("symbol") if isinstance(data, dict) else None
        if isinstance(claimed, str) and claimed and claimed != symbol:
            continue  # another symbol's file — its own pass reports problems
        problems = validate_snapshot_dict(data)
        if problems:
            notes.append(f"external: invalid {path.name} ({problems[0]})")
            continue
        if data["source"] not in allowed_sources:
            notes.append(f"external: unknown source {data['source']!r} in {path.name}")
            continue
        captured = int(data["captured_at_ms"])
        if captured > as_of_ms:
            notes.append(f"external: {path.name} captured in the future — skipped")
            continue
        if (as_of_ms - captured) / _MS_PER_HOUR > max_age_hours:
            if stale_latest_ms is None or captured > stale_latest_ms:
                stale_latest_ms = captured
            continue
        key = (
            str(data["source"]),
            str(data.get("venue") or ""),
            str(data["scope"] or ""),
            str(data["panel"]),
            normalize_window(data["window"]),
        )
        kept = fresh.get(key)
        if kept is None or captured > int(kept["captured_at_ms"]):
            fresh[key] = data
    if not fresh:
        if stale_latest_ms is not None:
            age_days = (as_of_ms - stale_latest_ms) / _MS_PER_HOUR / 24.0
            notes.append(
                f"external context stale (latest {age_days:.1f}d) — re-drop screenshots"
            )
        return None, notes
    if atr14 <= 0:
        notes.append("external: ATR unavailable — block omitted")
        return None, notes
    snapshots = [
        _build_snapshot(
            fresh[key], ref_close, atr14, as_of_ms, max_rows_per_side, notes
        )
        for key in sorted(fresh)
    ]
    return ExternalState(snapshots=snapshots), notes
