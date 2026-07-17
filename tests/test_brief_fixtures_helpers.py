"""brief_cfg factory: isolation defaults every brief test relies on."""

from pathlib import Path

from tests._brief_fixtures import brief_cfg


def test_brief_cfg_isolates_external_dir_by_default() -> None:
    cfg = brief_cfg(("BTCUSDT",), 1_704_067_200_000)
    assert cfg.external_dir == Path("tests/no-such-external-context")
    assert not cfg.external_dir.exists()
    assert cfg.stats_days == 60


def test_brief_cfg_overrides_pass_through(tmp_path: Path) -> None:
    cfg = brief_cfg(
        ("BTCUSDT",),
        1_704_067_200_000,
        stats_days=90,
        external_dir=tmp_path,
        ledger_path=tmp_path / "calls.jsonl",
    )
    assert cfg.stats_days == 90
    assert cfg.external_dir == tmp_path
    assert cfg.ledger_path == tmp_path / "calls.jsonl"
