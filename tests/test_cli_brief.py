"""CLI wiring: run_brief_cmd prints the brief; --json/--markdown write files."""

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb

from analytics.data_store import init_schema
from cli.brief import run_brief_cmd
from tests._brief_fixtures import START_MS, seed_symbol

AS_OF_ISO = "2024-03-01T00:00:00Z"  # START_MS + 60 days


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    conn = duckdb.connect(str(db))
    init_schema(conn)
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    conn.close()
    return db


def _args(db: Path, tmp_path: Path, **overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "symbols": ["BTCUSDT"],
        "db": str(db),
        "as_of": AS_OF_ISO,
        "days": 60,
        "json": None,
        "markdown": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_run_brief_cmd_prints_brief(tmp_path: Path, capsys: Any) -> None:
    db = _make_db(tmp_path)
    run_brief_cmd(_args(db, tmp_path))
    out = capsys.readouterr().out
    assert "BUIBUI DAILY BRIEF" in out
    assert "── BTCUSDT" in out


def test_run_brief_cmd_writes_outputs(tmp_path: Path, capsys: Any) -> None:
    db = _make_db(tmp_path)
    json_path = tmp_path / "brief.json"
    md_path = tmp_path / "brief.md"
    run_brief_cmd(_args(db, tmp_path, json=str(json_path), markdown=str(md_path)))
    capsys.readouterr()
    data = json.loads(json_path.read_text())
    assert data["panels"][0]["symbol"] == "BTCUSDT"
    assert "BUIBUI DAILY BRIEF" in md_path.read_text()


def test_run_brief_cmd_all_panels_failed_exits_1(tmp_path: Path, capsys: Any) -> None:
    db = _make_db(tmp_path)
    import pytest

    with pytest.raises(SystemExit) as exc_info:
        run_brief_cmd(_args(db, tmp_path, symbols=["NODATAUSDT"]))
    assert exc_info.value.code == 1
