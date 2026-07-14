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

SCHEMA_VERSION = "external-levels-v1"
ALLOWED_PANELS = ("liq_heatmap", "book_heatmap", "liq_map")
ALLOWED_KINDS = ("liq", "book")
ALLOWED_INTENSITIES = ("high", "med", "low")
ALLOWED_SCOPES = ("pair", "agg")
_SPOT_DEVIATION_FRAC = 0.10
_MS_PER_HOUR = 3_600_000

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
    """
    if not isinstance(data, dict):
        return ["snapshot is not a JSON object"]
    problems: list[str] = []
    keys = set(data)
    missing = _REQUIRED_KEYS - keys
    unknown = keys - _REQUIRED_KEYS
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
