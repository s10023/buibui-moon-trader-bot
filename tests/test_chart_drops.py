"""tools/chart_drops — filename parse, sha256 ledger, scan, write, CLI."""

import json
import os
import subprocess
import sys
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
    assert parsed == ("coinglass", None, "BTCUSDT", _myt_ms(2026, 7, 14, 9, 30))


def test_parse_date_only_and_no_ts() -> None:
    assert parse_drop_filename("mmt_ETHUSDT_20260714.jpg") == (
        "mmt",
        None,
        "ETHUSDT",
        _myt_ms(2026, 7, 14),
    )
    assert parse_drop_filename("mmt_ETHUSDT.jpeg") == ("mmt", None, "ETHUSDT", None)


def test_parse_rejects_bad_names() -> None:
    assert parse_drop_filename("hyblock_BTCUSDT.png") is None  # unknown source
    assert parse_drop_filename("coinglass_btcusdt.png") is None  # lowercase symbol
    assert parse_drop_filename("coinglass_BTCUSDT_2026.png") is None  # bad ts
    assert parse_drop_filename("coinglass_BTCUSDT.gif") is None  # bad ext
    assert parse_drop_filename("random.png") is None


def test_parse_accepts_and_discards_a_trailing_label() -> None:
    """The real 2026-08-24 burst capture parses, and the label is dropped."""
    labelled = parse_drop_filename("coinglass_BTCUSDT_20260824-1705_Map_1y.png")
    assert labelled == ("coinglass", None, "BTCUSDT", _myt_ms(2026, 8, 24, 17, 5))
    # Byte-identical to the same drop with no label: the label carries no
    # meaning, so it must not reach any field a consumer reads.
    assert labelled == parse_drop_filename("coinglass_BTCUSDT_20260824-1705.png")


def test_all_five_same_minute_windows_parse_identically() -> None:
    """Five panels grabbed in one minute all parse; `path` is what tells them apart.

    `window` is vision-derived and is part of `load_external_state`'s dedup
    key, so it must NOT be inferred from these labels.
    """
    parsed = [
        parse_drop_filename(f"coinglass_BTCUSDT_20260824-1705_Map_{w}.png")
        for w in ("7d", "30d", "90d", "180d", "1y")
    ]
    assert all(
        p == ("coinglass", None, "BTCUSDT", _myt_ms(2026, 8, 24, 17, 5)) for p in parsed
    )


def test_date_only_stamp_with_a_time_shaped_label_is_rejected() -> None:
    """#827: the underscore form backdated a real 18:00 capture to midnight."""
    assert parse_drop_filename("coinglass_BTCUSDT_20260827_HeatMap_1d_1800.png") is None
    assert parse_drop_filename("coinglass_BTCUSDT_20260827_1800.png") is None
    # The dash form is the convention and still parses with its time.
    assert parse_drop_filename("coinglass_BTCUSDT_20260827-1800_HeatMap_1d.png") == (
        "coinglass",
        None,
        "BTCUSDT",
        _myt_ms(2026, 8, 27, 18, 0),
    )
    # A date-only stamp with a non-time label still parses (midnight is declared).
    assert parse_drop_filename("coinglass_BTCUSDT_20260827_Map_1y.png") is not None


def test_label_requires_a_timestamp_and_a_leading_letter() -> None:
    """The label must sit AFTER a timestamp and start with a letter.

    ⚠ **The two restrictions are not equally load-bearing, and the measurement
    says which is which.** Mutating the regex three ways (2026-08-24):

    * drop the after-a-timestamp rule -> 2 of the 3 names below start parsing;
    * drop the leading-letter rule alone -> **0 leak**, because a label can only
      appear after a valid 8-digit stamp, so the leading-letter rule never gets
      asked about "..._2026.png";
    * drop BOTH -> all 3 leak.

    So the after-a-timestamp rule carries every rejection today and the
    leading-letter rule is a BACKSTOP: it is what keeps the malformed-stamp
    rejection if the first rule is ever relaxed. Keep it, but do not claim a
    test distinguishes it in isolation — none can while both stand.
    """
    # Undated drop: would parse as symbol=BTCUSDT + label=Map_1y and fall back
    # to the file's mtime, silently inventing a capture time.
    assert parse_drop_filename("coinglass_BTCUSDT_Map_1y.png") is None
    # Malformed stamp: would be read as a label, same silent mtime fallback.
    assert parse_drop_filename("coinglass_BTCUSDT_2026.png") is None
    # Mis-separated symbol: would parse as symbol="BTC" + label="USDT" — the
    # WRONG symbol, and symbol is authoritative for routing the snapshot.
    assert parse_drop_filename("coinglass_BTC_USDT.png") is None


def test_scan_lists_same_minute_burst_as_pending_not_unparseable(
    tmp_path: Path,
) -> None:
    drop = tmp_path / "chart-drops"
    drop.mkdir()
    for i, w in enumerate(("7d", "30d", "90d", "180d", "1y")):
        (drop / f"coinglass_BTCUSDT_20260824-1705_Map_{w}.png").write_bytes(
            f"img-{i}".encode()
        )
    pending, unparseable = scan_drops(drop, tmp_path / "ledger.json")
    assert unparseable == []
    assert len(pending) == 5
    # Distinct images => distinct hashes => five independent ingests.
    assert len({p.sha256 for p in pending}) == 5
    assert {p.symbol for p in pending} == {"BTCUSDT"}


def test_parse_drop_filename_uppercase_extension() -> None:
    parsed = parse_drop_filename("coinglass_BTCUSDT_20260716-1040.PNG")
    assert parsed is not None
    source, venue, symbol, ts_ms = parsed
    assert (source, venue, symbol) == ("coinglass", None, "BTCUSDT")
    assert ts_ms is not None


def test_parse_drop_filename_mixed_case_jpeg() -> None:
    parsed = parse_drop_filename("mmt_ETHUSDT.Jpeg")
    assert parsed is not None
    assert parsed[1] is None  # venue
    # source/symbol case rules unchanged:
    assert parse_drop_filename("Coinglass_BTCUSDT.png") is None
    assert parse_drop_filename("coinglass_btcusdt.png") is None


def test_parse_drop_filename_with_venue_token() -> None:
    parsed = parse_drop_filename("coinglass-hyperliquid_BTCUSDT_20260716-1040.png")
    assert parsed is not None
    source, venue, symbol, ts_ms = parsed
    assert (source, venue, symbol) == ("coinglass", "hyperliquid", "BTCUSDT")
    assert ts_ms is not None


def test_parse_drop_filename_without_venue_is_none_venue() -> None:
    parsed = parse_drop_filename("coinglass_BTCUSDT_20260716-1040.png")
    assert parsed is not None
    assert parsed[1] is None


def test_scan_drops_carries_venue(tmp_path: Path) -> None:
    drop = tmp_path / "drops"
    drop.mkdir()
    (drop / "coinglass-hyperliquid_BTCUSDT_20260716-1040.png").write_bytes(b"i")
    pending, unparseable = scan_drops(drop, tmp_path / "ledger.json")
    assert unparseable == []
    assert pending[0].venue == "hyperliquid"


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
    ledger.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt ledger"):
        load_ledger(ledger)


def test_scan_drops(tmp_path: Path) -> None:
    drop = tmp_path / "drops"
    drop.mkdir()
    (drop / "coinglass_BTCUSDT_20260714-0930.png").write_bytes(b"img-a")
    (drop / "weird name.png").write_bytes(b"img-b")
    (drop / "notes.txt").write_text("not an image", encoding="utf-8")
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


def test_snapshot_filename_includes_venue() -> None:
    data = _snapshot()
    data["venue"] = "hyperliquid"
    name = snapshot_filename(data)
    assert name.startswith("coinglass-hyperliquid_")
    data.pop("venue")
    assert snapshot_filename(data).startswith("coinglass_")


def test_write_snapshot_validates(tmp_path: Path) -> None:
    path = write_snapshot(_snapshot(), tmp_path / "out")
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["verified"] is True
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
    json_file.write_text(json.dumps(_snapshot()), encoding="utf-8")
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
    (drop / "done").write_text("not a dir", encoding="utf-8")
    snap_file = tmp_path / "snap.json"
    snap_file.write_text(
        json.dumps(_snapshot()), encoding="utf-8"
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
    (drop / "done").write_text(
        "blocker", encoding="utf-8"
    )  # file blocks mkdir -> move raises
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


class TestBareInvocation:
    """`/ingest-charts` shells out to this scanner by hand, and the Make targets set
    PYTHONPATH — so a green Make target proves nothing about the bare form. Per ST129
    the guarantee is THIS test, not the bootstrap line in the module."""

    def test_bare_invocation_works(self, tmp_path: Path) -> None:
        drop = tmp_path / "drops"
        drop.mkdir()
        proc = subprocess.run(
            [
                sys.executable,
                "tools/chart_drops.py",
                "scan",
                "--drop-dir",
                str(drop),
                "--ledger",
                str(tmp_path / "ledger.json"),
            ],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        )

        assert proc.returncode == 0, proc.stderr
        assert (
            "analytics" not in proc.stderr
        )  # the ModuleNotFoundError it used to die on
