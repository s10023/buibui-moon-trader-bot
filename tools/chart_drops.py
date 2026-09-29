"""Chart-drop helper for /ingest-charts (M3 external context).

Writer-side logic the skill shells out to: filename parsing, the sha256
processed-ledger, schema-validated snapshot writes, and moving handled
images to done/. The read side lives in analytics/brief/external.py —
this module imports its validator so writer and reader share one contract.

Run via: PYTHONPATH=. poetry run python tools/chart_drops.py scan|write|mark
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# A bare `python3 tools/chart_drops.py` puts `tools/` on sys.path rather than the repo
# root, so the `analytics.*` import below died with ModuleNotFoundError — only the Make
# target and an explicit `PYTHONPATH=.` worked. Per ST129 the guarantee is
# `test_bare_invocation_works`, not this line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.brief.external import validate_snapshot_dict  # noqa: E402

DEFAULT_DROP_DIR = Path("docs/plans/chart-drops")
DEFAULT_OUT_DIR = Path("docs/plans/external-context")
DEFAULT_LEDGER = Path(".cache/chart-drops/processed.json")
ALLOWED_SOURCES = ("coinglass", "mmt")
OUTCOMES = ("written", "skipped", "dropped")
_MYT = timezone(timedelta(hours=8))
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")
_NAME_RE = re.compile(
    r"^(?P<source>[a-z0-9]+)(?:-(?P<venue>[a-z0-9]+))?_(?P<symbol>[A-Z0-9]+)"
    r"(?:_(?P<ts>\d{8}(?:-\d{4})?)(?:_(?P<label>[A-Za-z][A-Za-z0-9_]*))?)?"
    r"\.(?i:png|jpg|jpeg)$"
)


@dataclass(frozen=True)
class PendingDrop:
    path: str
    sha256: str
    source: str
    venue: str | None
    symbol: str
    captured_at_ms: int
    ts_from_filename: bool


def parse_drop_filename(
    name: str, allowed_sources: tuple[str, ...] = ALLOWED_SOURCES
) -> tuple[str, str | None, str, int | None] | None:
    """(source, venue | None, symbol, captured_at_ms | None) or None if unparseable.

    venue is an optional dash-suffixed token on the source segment (e.g.
    "coinglass-hyperliquid"); absent when the drop has no dash. Timestamp
    is the operator's wall clock — MYT (fixed UTC+8).

    A trailing free-text label after the timestamp is ACCEPTED AND DISCARDED
    (e.g. "coinglass_BTCUSDT_20260824_Map_1y_1705.png"). Its only job is to
    make a burst capture nameable: the scheme's sole uniqueness mechanism was
    the timestamp's minute, so five panels grabbed in the same minute could
    not be given distinct names, and the operator's natural workaround —
    appending the window — made all five unparseable instead.

    ⚠ **The label is deliberately NOT a source of truth for `window`.** That
    field is read off the chart by vision and corrected by the operator at the
    review gate, and it is part of `load_external_state`'s dedup key, so a
    filename that disagreed with the image would silently split or merge
    snapshots. The filename stays authoritative for source/venue/symbol only;
    the label carries no meaning and nothing downstream reads it. Callers that
    need to tell two same-minute drops apart use ``PendingDrop.path``.

    The label is allowed only AFTER a timestamp and must start with a letter.
    The first rule is what preserves every existing rejection — a malformed
    stamp ("..._2026.png") and a mis-separated symbol ("coinglass_BTC_USDT.png")
    both stay unparseable because neither offers a valid 8-digit stamp for a
    label to follow. The leading-letter rule leaks nothing on its own and is a
    deliberate BACKSTOP for the day the first rule is relaxed; the measurement
    behind that split is in ``test_label_requires_a_timestamp_and_a_leading_letter``.
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
    return match.group("source"), match.group("venue"), match.group("symbol"), ts_ms


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_ledger(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # A corrupt dedup ledger must fail loudly: silently treating it as
        # empty would re-ingest every already-processed image on next scan.
        raise ValueError(f"corrupt ledger {path} — fix or delete it: {exc}") from exc
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
    path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")


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
        source, venue, symbol, ts_ms = parsed
        captured = ts_ms if ts_ms is not None else int(path.stat().st_mtime * 1000)
        pending.append(
            PendingDrop(
                path=str(path),
                sha256=sha,
                source=source,
                venue=venue,
                symbol=symbol,
                captured_at_ms=captured,
                ts_from_filename=ts_ms is not None,
            )
        )
    return pending, unparseable


def snapshot_filename(data: dict[str, Any]) -> str:
    ts = datetime.fromtimestamp(data["captured_at_ms"] / 1000, tz=_MYT)
    window = f"_{data['window']}" if data["window"] else ""
    src = data["source"] + (f"-{data['venue']}" if data.get("venue") else "")
    return (
        f"{src}_{data['panel']}{window}_{data['symbol']}"
        f"_{ts.strftime('%Y%m%d-%H%M')}.json"
    )


def write_snapshot(snapshot: dict[str, Any], out_dir: Path = DEFAULT_OUT_DIR) -> Path:
    problems = validate_snapshot_dict(snapshot)
    if problems:
        raise ValueError("invalid snapshot: " + "; ".join(problems))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / snapshot_filename(snapshot)
    path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    return path


def move_to_done(image_path: Path) -> Path:
    done = image_path.parent / "done"
    done.mkdir(exist_ok=True)
    target = done / image_path.name
    counter = 1
    while target.exists():
        target = done / f"{image_path.stem}_{counter}{image_path.suffix}"
        counter += 1
    image_path.rename(target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Chart-drop helper for /ingest-charts (scan / write / mark)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_scan = sub.add_parser("scan", help="List unprocessed drops as JSON")
    p_scan.add_argument("--drop-dir", type=Path, default=DEFAULT_DROP_DIR)
    p_scan.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    p_write = sub.add_parser("write", help="Validate + write an approved snapshot")
    p_write.add_argument("--json-file", type=Path, required=True)
    p_write.add_argument("--image", type=Path, required=True)
    p_write.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    p_write.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    p_mark = sub.add_parser("mark", help="Record a skipped/dropped image")
    p_mark.add_argument("--image", type=Path, required=True)
    p_mark.add_argument("--outcome", choices=["skipped", "dropped"], required=True)
    p_mark.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args(argv)
    now_ms = int(time.time() * 1000)
    if args.cmd == "scan":
        pending, unparseable = scan_drops(args.drop_dir, args.ledger)
        print(
            json.dumps(
                {"pending": [asdict(p) for p in pending], "unparseable": unparseable},
                indent=2,
            )
        )
        return 0
    # Ledger consistency: mark a hash only AFTER its outcome is final —
    # the move is part of finalizing, so sha/name are captured pre-move
    # (the file leaves this path), the move runs, THEN the ledger is
    # written. A failed move raises with the ledger untouched, so the
    # image stays visible to the next scan.
    sha = file_sha256(args.image)
    name = args.image.name
    if args.cmd == "write":
        snapshot = json.loads(args.json_file.read_text(encoding="utf-8"))
        path = write_snapshot(snapshot, args.out_dir)
        moved = move_to_done(args.image)
        mark_processed(args.ledger, sha, name, "written", now_ms)
        print(json.dumps({"written": str(path), "image_moved_to": str(moved)}))
        return 0
    moved = move_to_done(args.image)
    mark_processed(args.ledger, sha, name, args.outcome, now_ms)
    print(json.dumps({"marked": args.outcome, "image_moved_to": str(moved)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
