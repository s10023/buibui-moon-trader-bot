"""Chart-drop helper for /ingest-charts (M3 external context).

Writer-side logic the skill shells out to: filename parsing, the sha256
processed-ledger, schema-validated snapshot writes, and moving handled
images to done/. The read side lives in analytics/brief/external.py —
this module imports its validator so writer and reader share one contract.

Run via: PYTHONPATH=. poetry run python tools/chart_drops.py scan|write|mark
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_DROP_DIR = Path("docs/plans/chart-drops")
DEFAULT_OUT_DIR = Path("docs/plans/external-context")
DEFAULT_LEDGER = Path(".cache/chart-drops/processed.json")
ALLOWED_SOURCES = ("coinglass", "mmt")
OUTCOMES = ("written", "skipped", "dropped")
_MYT = timezone(timedelta(hours=8))
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")
_NAME_RE = re.compile(
    r"^(?P<source>[a-z0-9]+)_(?P<symbol>[A-Z0-9]+)"
    r"(?:_(?P<ts>\d{8}(?:-\d{4})?))?\.(?:png|jpg|jpeg)$"
)


@dataclass(frozen=True)
class PendingDrop:
    path: str
    sha256: str
    source: str
    symbol: str
    captured_at_ms: int
    ts_from_filename: bool


def parse_drop_filename(
    name: str, allowed_sources: tuple[str, ...] = ALLOWED_SOURCES
) -> tuple[str, str, int | None] | None:
    """(source, symbol, captured_at_ms | None) or None if unparseable.

    Timestamp is the operator's wall clock — MYT (fixed UTC+8).
    """
    match = _NAME_RE.match(name)
    if match is None or match.group("source") not in allowed_sources:
        return None
    ts_ms: int | None = None
    ts = match.group("ts")
    if ts is not None:
        fmt = "%Y%m%d-%H%M" if "-" in ts else "%Y%m%d"
        try:
            parsed = datetime.strptime(ts, fmt).replace(tzinfo=_MYT)
        except ValueError:
            return None
        ts_ms = int(parsed.timestamp() * 1000)
    return match.group("source"), match.group("symbol"), ts_ms


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_ledger(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text())
    return data if isinstance(data, dict) else {}


def mark_processed(
    path: Path, sha256: str, filename: str, outcome: str, ingested_at_ms: int
) -> None:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome {outcome!r} not in {OUTCOMES}")
    ledger = load_ledger(path)
    ledger[sha256] = {
        "filename": filename,
        "ingested_at_ms": ingested_at_ms,
        "outcome": outcome,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2) + "\n")


def scan_drops(
    drop_dir: Path = DEFAULT_DROP_DIR,
    ledger_path: Path = DEFAULT_LEDGER,
    allowed_sources: tuple[str, ...] = ALLOWED_SOURCES,
) -> tuple[list[PendingDrop], list[str]]:
    """(pending, unparseable_names). done/ and non-images are ignored."""
    if not drop_dir.is_dir():
        return [], []
    ledger = load_ledger(ledger_path)
    pending: list[PendingDrop] = []
    unparseable: list[str] = []
    for path in sorted(drop_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        sha = file_sha256(path)
        if sha in ledger:
            continue
        parsed = parse_drop_filename(path.name, allowed_sources)
        if parsed is None:
            unparseable.append(path.name)
            continue
        source, symbol, ts_ms = parsed
        captured = ts_ms if ts_ms is not None else int(path.stat().st_mtime * 1000)
        pending.append(
            PendingDrop(
                path=str(path),
                sha256=sha,
                source=source,
                symbol=symbol,
                captured_at_ms=captured,
                ts_from_filename=ts_ms is not None,
            )
        )
    return pending, unparseable
