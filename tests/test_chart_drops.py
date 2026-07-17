"""tools/chart_drops — filename parse, sha256 ledger, scan, write, CLI."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tools.chart_drops import (
    file_sha256,
    load_ledger,
    main,
    mark_processed,
    move_to_done,
    parse_drop_filename,
    scan_drops,
    snapshot_filename,
    write_snapshot,
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


def test_parse_drop_filename_uppercase_extension() -> None:
    parsed = parse_drop_filename("coinglass_BTCUSDT_20260716-1040.PNG")
    assert parsed is not None
    source, symbol, ts_ms = parsed
    assert (source, symbol) == ("coinglass", "BTCUSDT")
    assert ts_ms is not None


def test_parse_drop_filename_mixed_case_jpeg() -> None:
    assert parse_drop_filename("mmt_ETHUSDT.Jpeg") is not None
    # source/symbol case rules unchanged:
    assert parse_drop_filename("Coinglass_BTCUSDT.png") is None
    assert parse_drop_filename("coinglass_btcusdt.png") is None


def test_ledger_roundtrip(tmp_path: Path) -> None:
    ledger = tmp_path / "sub" / "processed.json"
    assert load_ledger(ledger) == {}
    mark_processed(ledger, "abc123", "a.png", "written", 1_000)
    mark_processed(ledger, "def456", "b.png", "dropped", 2_000)
    data = load_ledger(ledger)
    assert data["abc123"]["outcome"] == "written"
    assert data["def456"]["ingested_at_ms"] == 2_000


def test_load_ledger_corrupt_raises_actionable_error(tmp_path: Path) -> None:
    ledger = tmp_path / "processed.json"
    ledger.write_text("{broken")
    with pytest.raises(ValueError, match="corrupt ledger"):
        load_ledger(ledger)


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


def test_scan_drops_picks_up_uppercase_png(tmp_path: Path) -> None:
    drop = tmp_path / "drops"
    drop.mkdir()
    (drop / "coinglass_BTCUSDT_20260716-1040.PNG").write_bytes(b"img")
    pending, unparseable = scan_drops(drop, tmp_path / "ledger.json")
    assert unparseable == []
    assert len(pending) == 1
    assert pending[0].symbol == "BTCUSDT"


def _snapshot(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema": "external-levels-v1",
        "source": "coinglass",
        "symbol": "BTCUSDT",
        "panel": "liq_heatmap",
        "window": "24h",
        "scope": "pair",
        "captured_at_ms": _myt_ms(2026, 7, 14, 9, 30),
        "ingested_at_ms": _myt_ms(2026, 7, 14, 11, 0),
        "verified": True,
        "spot_price_hint": 62_452.0,
        "clusters": [
            {
                "price_lo": 63_200.0,
                "price_hi": 63_400.0,
                "kind": "liq",
                "intensity": "high",
                "label": "",
            }
        ],
        "notes": "",
    }
    data.update(overrides)
    return data


def test_snapshot_filename_includes_window() -> None:
    assert (
        snapshot_filename(_snapshot())
        == "coinglass_liq_heatmap_24h_BTCUSDT_20260714-0930.json"
    )
    assert (
        snapshot_filename(_snapshot(window=None, panel="liq_map"))
        == "coinglass_liq_map_BTCUSDT_20260714-0930.json"
    )


def test_write_snapshot_validates(tmp_path: Path) -> None:
    path = write_snapshot(_snapshot(), tmp_path / "out")
    assert path.is_file()
    assert json.loads(path.read_text())["verified"] is True
    try:
        write_snapshot(_snapshot(panel="oi_chart"), tmp_path / "out")
    except ValueError as exc:
        assert "invalid snapshot" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_move_to_done_collision(tmp_path: Path) -> None:
    img = tmp_path / "coinglass_BTCUSDT.png"
    img.write_bytes(b"one")
    moved = move_to_done(img)
    assert moved == tmp_path / "done" / "coinglass_BTCUSDT.png"
    img2 = tmp_path / "coinglass_BTCUSDT.png"
    img2.write_bytes(b"two")
    moved2 = move_to_done(img2)
    assert moved2 == tmp_path / "done" / "coinglass_BTCUSDT_1.png"


def test_cli_scan_write_mark(tmp_path: Path, capsys: Any) -> None:
    drop = tmp_path / "drops"
    drop.mkdir()
    img = drop / "coinglass_BTCUSDT_20260714-0930.png"
    img.write_bytes(b"img")
    ledger = tmp_path / "ledger.json"
    out_dir = tmp_path / "ext"
    assert main(["scan", "--drop-dir", str(drop), "--ledger", str(ledger)]) == 0
    scanned = json.loads(capsys.readouterr().out)
    assert scanned["unparseable"] == []
    assert scanned["pending"][0]["symbol"] == "BTCUSDT"
    json_file = tmp_path / "approved.json"
    json_file.write_text(json.dumps(_snapshot()))
    assert (
        main(
            [
                "write",
                "--json-file",
                str(json_file),
                "--image",
                str(img),
                "--out-dir",
                str(out_dir),
                "--ledger",
                str(ledger),
            ]
        )
        == 0
    )
    assert (out_dir / "coinglass_liq_heatmap_24h_BTCUSDT_20260714-0930.json").is_file()
    assert not img.exists() and (drop / "done" / img.name).is_file()
    assert list(load_ledger(ledger).values())[0]["outcome"] == "written"
    img_b = drop / "mmt_ETHUSDT.png"
    img_b.write_bytes(b"other")
    assert (
        main(
            [
                "mark",
                "--image",
                str(img_b),
                "--outcome",
                "dropped",
                "--ledger",
                str(ledger),
            ]
        )
        == 0
    )
    outcomes = {v["filename"]: v["outcome"] for v in load_ledger(ledger).values()}
    assert outcomes["mmt_ETHUSDT.png"] == "dropped"
    assert (drop / "done" / "mmt_ETHUSDT.png").is_file()


def test_mark_processed_rejects_unknown_outcome(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outcome"):
        mark_processed(tmp_path / "l.json", "sha", "f.png", "exploded", 1)


def test_write_branch_move_failure_fails_open(tmp_path: Path) -> None:
    # done/ existing as a FILE makes move_to_done raise; the snapshot is
    # already written (fail-open), but the ledger MUST stay unmarked so
    # the image is still visible to the next scan.
    drop = tmp_path / "drops"
    drop.mkdir()
    image = drop / "coinglass_BTCUSDT_20260716-1040.png"
    image.write_bytes(b"img")
    (drop / "done").write_text("not a dir")
    snap_file = tmp_path / "snap.json"
    snap_file.write_text(
        json.dumps(_snapshot())
    )  # the file's existing builder (line 96)
    out_dir = tmp_path / "out"
    ledger = tmp_path / "ledger.json"
    with pytest.raises(OSError):
        main(
            [
                "write",
                "--json-file",
                str(snap_file),
                "--image",
                str(image),
                "--out-dir",
                str(out_dir),
                "--ledger",
                str(ledger),
            ]
        )
    assert image.exists()  # image untouched in the drop dir
    assert load_ledger(ledger) == {}  # unmarked -> next scan retries


def test_cli_move_failure_leaves_ledger_unmarked(tmp_path: Path) -> None:
    drop = tmp_path / "drops"
    drop.mkdir()
    img = drop / "coinglass_BTCUSDT_20260714-0930.png"
    img.write_bytes(b"img")
    (drop / "done").write_text("blocker")  # file blocks mkdir -> move raises
    ledger = tmp_path / "ledger.json"
    with pytest.raises(OSError):
        main(
            [
                "mark",
                "--image",
                str(img),
                "--outcome",
                "dropped",
                "--ledger",
                str(ledger),
            ]
        )
    assert load_ledger(ledger) == {}  # hash marked only after outcome is final
    assert img.is_file()  # image stays visible to the next scan
