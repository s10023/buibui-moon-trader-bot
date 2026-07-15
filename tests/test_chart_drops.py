"""tools/chart_drops — filename parse, sha256 ledger, scan, write, CLI."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.chart_drops import (
    file_sha256,
    load_ledger,
    mark_processed,
    parse_drop_filename,
    scan_drops,
)

MYT = timezone(timedelta(hours=8))


def _myt_ms(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=MYT).timestamp() * 1000)


def test_parse_full_name() -> None:
    parsed = parse_drop_filename("coinglass_BTCUSDT_20260714-0930.png")
    assert parsed == ("coinglass", "BTCUSDT", _myt_ms(2026, 7, 14, 9, 30))


def test_parse_date_only_and_no_ts() -> None:
    assert parse_drop_filename("mmt_ETHUSDT_20260714.jpg") == (
        "mmt",
        "ETHUSDT",
        _myt_ms(2026, 7, 14),
    )
    assert parse_drop_filename("mmt_ETHUSDT.jpeg") == ("mmt", "ETHUSDT", None)


def test_parse_rejects_bad_names() -> None:
    assert parse_drop_filename("hyblock_BTCUSDT.png") is None  # unknown source
    assert parse_drop_filename("coinglass_btcusdt.png") is None  # lowercase symbol
    assert parse_drop_filename("coinglass_BTCUSDT_2026.png") is None  # bad ts
    assert parse_drop_filename("coinglass_BTCUSDT.gif") is None  # bad ext
    assert parse_drop_filename("random.png") is None


def test_ledger_roundtrip(tmp_path: Path) -> None:
    ledger = tmp_path / "sub" / "processed.json"
    assert load_ledger(ledger) == {}
    mark_processed(ledger, "abc123", "a.png", "written", 1_000)
    mark_processed(ledger, "def456", "b.png", "dropped", 2_000)
    data = load_ledger(ledger)
    assert data["abc123"]["outcome"] == "written"
    assert data["def456"]["ingested_at_ms"] == 2_000


def test_scan_drops(tmp_path: Path) -> None:
    drop = tmp_path / "drops"
    drop.mkdir()
    (drop / "coinglass_BTCUSDT_20260714-0930.png").write_bytes(b"img-a")
    (drop / "weird name.png").write_bytes(b"img-b")
    (drop / "notes.txt").write_text("not an image")
    done = drop / "done"
    done.mkdir()
    (done / "coinglass_ETHUSDT_20260713.png").write_bytes(b"img-old")
    ledger = tmp_path / "processed.json"
    pending, unparseable = scan_drops(drop, ledger)
    assert unparseable == ["weird name.png"]
    assert len(pending) == 1
    item = pending[0]
    assert item.source == "coinglass" and item.symbol == "BTCUSDT"
    assert item.ts_from_filename is True
    assert item.sha256 == file_sha256(drop / "coinglass_BTCUSDT_20260714-0930.png")
    # ledger-marked files disappear from the next scan
    mark_processed(
        ledger, item.sha256, "coinglass_BTCUSDT_20260714-0930.png", "written", 1
    )
    pending2, _ = scan_drops(drop, ledger)
    assert pending2 == []


def test_scan_mtime_fallback(tmp_path: Path) -> None:
    drop = tmp_path / "drops"
    drop.mkdir()
    img = drop / "mmt_SOLUSDT.png"
    img.write_bytes(b"img")
    pending, _ = scan_drops(drop, tmp_path / "ledger.json")
    assert pending[0].ts_from_filename is False
    assert pending[0].captured_at_ms == int(img.stat().st_mtime * 1000)
