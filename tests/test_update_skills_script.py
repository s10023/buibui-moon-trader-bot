"""`.github/scripts/update-skills.sh` must accept CRLF SKILL.md files (#900)."""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1] / ".github" / "scripts" / "update-skills.sh"
)
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not on PATH")


def _run(skills_dir: Path) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run(
        [BASH, str(SCRIPT)],
        env={"SKILLS_DIR": skills_dir.as_posix(), "PATH": str(Path(BASH).parent)},
        capture_output=True,
        text=True,
        check=False,
    )


def _skill(root: Path, name: str, body: bytes) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_bytes(body)


def test_crlf_frontmatter_passes(tmp_path: Path) -> None:
    _skill(tmp_path, "crlf", b"---\r\nname: crlf\r\n---\r\nbody\r\n")
    _skill(tmp_path, "lf", b"---\nname: lf\n---\nbody\n")
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout


def test_missing_delimiters_still_fail(tmp_path: Path) -> None:
    """Teeth: stripping CR must not make the check accept anything."""
    _skill(tmp_path, "noopen", b"name: x\r\n---\r\n")
    _skill(tmp_path, "noclose", b"---\r\nname: y\r\nbody\r\n")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "noopen/SKILL.md missing opening" in result.stdout
    assert "noclose/SKILL.md missing closing" in result.stdout
