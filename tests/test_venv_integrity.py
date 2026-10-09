"""Tests for tools/venv_integrity.py (#963).

Each leg gets a TEETH case (inject the damage, the probe must name it) and a
SPECIFICITY case (a clean or legitimately-different venv must read clean), so a
pass cannot come from a probe that is blind or one that fires on everything.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from tools.venv_integrity import (
    check,
    missing_record_files,
    repair_hint,
    unmet_requirements,
)

REPO = Path(__file__).resolve().parent.parent


def _dist(
    site: Path,
    name: str,
    version: str = "1.0",
    requires: tuple[str, ...] = (),
    files: tuple[str, ...] = (),
    record_extra: tuple[str, ...] = (),
) -> None:
    """Install a fake distribution: its files on disk plus a RECORD naming them."""
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    meta = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    meta += [f"Requires-Dist: {r}" for r in requires]
    (info / "METADATA").write_text("\n".join(meta) + "\n", encoding="utf-8")
    pkg_files = files or (f"{name.replace('-', '_')}/__init__.py",)
    for f in pkg_files:
        p = site / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    rows = [*pkg_files, *record_extra, f"{info.name}/METADATA", f"{info.name}/RECORD"]
    (info / "RECORD").write_text("".join(f"{r},,\n" for r in rows), encoding="utf-8")


def _remove_whole(site: Path, name: str) -> None:
    """Delete a package AND its dist-info, as the 10-08 junction traversal did."""
    for p in site.glob(f"{name}*"):
        shutil.rmtree(p)


def _pyproject(tmp: Path, deps: tuple[str, ...]) -> Path:
    body = "[project]\nname = 'x'\ndependencies = [\n"
    body += "".join(f"    {d!r},\n" for d in deps) + "]\n"
    p = tmp / "pyproject.toml"
    p.write_text(body, encoding="utf-8")
    return p


def _healthy(tmp: Path) -> tuple[Path, Path]:
    site = tmp / "site"
    _dist(site, "aiohappyeyeballs", "2.7.1")
    _dist(
        site,
        "aiohttp",
        "3.14.3",
        requires=("aiohappyeyeballs>=2.5", "brotli; extra == 'speedups'"),
        files=("aiohttp/__init__.py", "aiohttp/hdrs.py", "aiohttp/_http.pyd"),
    )
    _dist(site, "duckdb", "1.5.6")
    return site, _pyproject(tmp, ("aiohttp (>=3.14.3,<4.0.0)", "duckdb>=1.5"))


class TestCleanVenv:
    def test_healthy_venv_reads_clean(self, tmp_path: Path) -> None:
        site, proj = _healthy(tmp_path)
        assert check([site], proj) == []
        assert repair_hint([]) == ""

    def test_pycache_and_pyc_entries_are_not_damage(self, tmp_path: Path) -> None:
        # pip's own RECORD lists bare __pycache__ dirs a clean venv lacks (39 on
        # the healthy laptop venv). Counting them would red every day.
        site = tmp_path / "site"
        _dist(
            site,
            "pip",
            "26.2.1",
            record_extra=(
                "pip/_internal/__pycache__",
                "pip/__pycache__/__init__.cpython-313.pyc",
                "pip\\_vendor\\__pycache__",
            ),
        )
        assert missing_record_files(site) == []

    def test_script_entry_outside_site_dir_resolves(self, tmp_path: Path) -> None:
        site = tmp_path / "Lib" / "site-packages"
        _dist(site, "pytest", "8.0", record_extra=("../../Scripts/pytest.exe",))
        (tmp_path / "Scripts").mkdir()
        (tmp_path / "Scripts" / "pytest.exe").write_text("", encoding="utf-8")
        assert missing_record_files(site) == []

    def test_unmet_extra_only_requirement_is_not_damage(self, tmp_path: Path) -> None:
        site, _ = _healthy(tmp_path)  # aiohttp's brotli is behind an extra
        assert unmet_requirements([site]) == []


class TestDamage:
    def test_partial_deletion_names_the_dist_and_its_version(
        self, tmp_path: Path
    ) -> None:
        # The 10-08 shape: "most of aiohttp" gone, its RECORD left behind.
        site, proj = _healthy(tmp_path)
        (site / "aiohttp" / "hdrs.py").unlink()
        (site / "aiohttp" / "_http.pyd").unlink()
        damage = check([site], proj)
        assert [(d.name, d.version, d.kind) for d in damage] == [
            ("aiohttp", "3.14.3", "files")
        ]
        assert "2 recorded file(s) missing" in damage[0].detail
        assert "hdrs.py" in damage[0].detail
        assert repair_hint(damage) == (
            "poetry run python -m pip install --force-reinstall --no-deps "
            "aiohttp==3.14.3"
        )

    def test_package_deleted_whole_is_caught_by_its_dependent(
        self, tmp_path: Path
    ) -> None:
        # The other 10-08 shape: aiohappyeyeballs gone WITH its dist-info, so no
        # RECORD is left to check. Only aiohttp's requirement can see it.
        site, proj = _healthy(tmp_path)
        _remove_whole(site, "aiohappyeyeballs")
        damage = check([site], proj)
        assert [(d.name, d.kind) for d in damage] == [
            ("aiohappyeyeballs", "requirement")
        ]
        assert "required by aiohttp" in damage[0].detail
        assert repair_hint(damage) == "poetry install --no-root"

    def test_direct_dependency_with_no_dependents_deleted_whole(
        self, tmp_path: Path
    ) -> None:
        site, proj = _healthy(tmp_path)
        _remove_whole(site, "duckdb")
        damage = check([site], proj)
        assert [(d.name, d.kind) for d in damage] == [("duckdb", "direct")]

    def test_missing_package_both_direct_and_required_reads_once(
        self, tmp_path: Path
    ) -> None:
        site = tmp_path / "site"
        _dist(site, "aiohttp", "3.14.3", requires=("aiohappyeyeballs>=2.5",))
        proj = _pyproject(tmp_path, ("aiohttp", "aiohappyeyeballs"))
        damage = check([site], proj)
        assert [(d.name, d.kind) for d in damage] == [("aiohappyeyeballs", "direct")]

    def test_mixed_damage_hint_carries_both_repairs(self, tmp_path: Path) -> None:
        site, proj = _healthy(tmp_path)
        (site / "aiohttp" / "hdrs.py").unlink()
        _remove_whole(site, "duckdb")
        hint = repair_hint(check([site], proj))
        assert "aiohttp==3.14.3" in hint
        assert hint.endswith("poetry install --no-root")


def test_bare_invocation_works() -> None:
    # The daily check imports it, but a hand-run must work without PYTHONPATH.
    proc = subprocess.run(
        [sys.executable, "tools/venv_integrity.py"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode in (0, 1), proc.stderr
    assert "venv integrity: clean" in proc.stdout or "repair:" in proc.stdout
