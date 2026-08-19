# AGENTS.md split + `docs/agents/` config — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `AGENTS.md` the agent-neutral instruction file and give this repo a single
machine-readable config, `docs/agents/surfaces.toml`, that the doc-drift checkers read instead
of each carrying its own hardcoded copy of the doc-surface list.

**Architecture:** A stdlib TOML loader (`tools/agents_config.py`) exposes one surface list whose
`roles` field derives the four tuples currently hardcoded across two tools. Consumers are
repointed *before* the prose moves, so the split itself becomes a two-file move plus one config
line — which is also the proof that the indirection works. The dead `handoff-size` leg is
rebuilt against real thresholds read from the same config.

**Tech Stack:** Python 3.11+ (stdlib `tomllib`), pytest, ruff, mypy strict, markdownlint.

**Spec:** `docs/superpowers/specs/2026-08-19-agents-md-split-design.md` — read it first; this
plan argues from it.

## Global Constraints

- **No new dependency.** `tomllib` is stdlib. The checkers run in CI's dependency-free
  `markdownlint` job, so any import that could fail there turns every leg into a SKIP.
- **A SKIP is not a PASS.** A missing or malformed config must surface as a FINDING. No caller
  may swallow `ConfigError` into a skip or a soft default.
- **mypy strict** — every function annotated, return types included (`-> None` on tests).
- **Prose is moved, never rewritten.** Task 5 is a permutation of existing lines; any wording
  change makes the mechanical verification useless.
- **Gates:** `make lint-py`, `make typecheck`, `make test`, `make lint-md`.
  `make test-regression` is **not required** — no path in the backtest surface is touched.
- ⚠ **`make lint-py` runs `ruff format .`, which reformats python fences inside `.md`.** This
  plan carries them. Expect one reformat commit of this file on the first Python task; land it
  rather than fighting it.
- ⚠ **Never edit the Python tree while `make test` is in flight.** Both run against the working
  tree and pytest imports at collection.

---

### Task 1: The config and its loader

**Files:**

- Create: `docs/agents/surfaces.toml`
- Create: `tools/agents_config.py`
- Test: `tests/test_agents_config.py`

**Interfaces:**

- Consumes: nothing.
- Produces: `load(root: Path | None = None) -> AgentsConfig`; `AgentsConfig.paths_with_role(role: str) -> tuple[str, ...]`;
  `AgentsConfig.budgets -> Budgets`; `AgentsConfig.flip_target: str`; `AgentsConfig.gh_user: str`;
  `ConfigError(RuntimeError)`; frozen dataclasses `Surface` and `Budgets`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the repo-specific agent config.

Two of these are mutation controls rather than behaviour tests. The list this
config replaces lived in four hardcoded copies, so "the checker returns the
right paths" proves nothing on its own — a surviving hardcoded tuple returns
them too. `test_dropping_a_role_shrinks_the_view` is the one that can tell the
difference.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.agents_config import ConfigError, load

MINIMAL = """
[repo]
flip_target = "owner/repo"
gh_user = "owner"

[budgets]
handoff_lines = 200
handoff_ceiling = 600
memory_state_bullets = 6
memory_state_ceiling = 8
memory_bytes_cap = 17408

[[surface]]
id = "agents_md"
path = "AGENTS.md"
purpose = "Agent-neutral project context"
roles = ["anchor", "enumerating"]

[[surface]]
id = "readme"
path = "README.md"
purpose = "User-facing overview"
roles = ["anchor"]
"""


def _write(tmp_path: Path, body: str) -> Path:
    (tmp_path / "docs" / "agents").mkdir(parents=True)
    (tmp_path / "docs" / "agents" / "surfaces.toml").write_text(body, encoding="utf-8")
    return tmp_path


def test_loads_repo_and_budget_scalars(tmp_path: Path) -> None:
    cfg = load(_write(tmp_path, MINIMAL))
    assert cfg.flip_target == "owner/repo"
    assert cfg.gh_user == "owner"
    assert cfg.budgets.handoff_lines == 200
    assert cfg.budgets.handoff_ceiling == 600
    assert cfg.budgets.memory_bytes_cap == 17408


def test_paths_with_role_filters_and_keeps_config_order(tmp_path: Path) -> None:
    cfg = load(_write(tmp_path, MINIMAL))
    assert cfg.paths_with_role("anchor") == ("AGENTS.md", "README.md")
    assert cfg.paths_with_role("enumerating") == ("AGENTS.md",)
    assert cfg.paths_with_role("nonexistent") == ()


def test_dropping_a_role_shrinks_the_view(tmp_path: Path) -> None:
    """The mutation control: a consumer reading a stale hardcoded copy passes
    every other test in this file and fails this one."""
    body = MINIMAL.replace('roles = ["anchor", "enumerating"]', 'roles = ["anchor"]')
    cfg = load(_write(tmp_path, body))
    assert cfg.paths_with_role("enumerating") == ()


def test_missing_config_raises_rather_than_defaulting(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing"):
        load(tmp_path)


def test_malformed_toml_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="malformed"):
        load(_write(tmp_path, "[repo\nflip_target = "))


def test_missing_required_key_raises(tmp_path: Path) -> None:
    body = MINIMAL.replace('gh_user = "owner"\n', "")
    with pytest.raises(ConfigError, match="required key"):
        load(_write(tmp_path, body))


def test_a_role_bearing_surface_must_have_a_concrete_path(tmp_path: Path) -> None:
    """Globs are for the skill's human-facing table. A role feeds a checker that
    opens the path, so a glob there would read as a missing file."""
    body = MINIMAL.replace('path = "README.md"', 'path_glob = ".claude/context/*.md"')
    with pytest.raises(ConfigError, match="concrete path"):
        load(_write(tmp_path, body))


def test_the_real_repo_config_loads(tmp_path: Path) -> None:
    """Guards against a committed config that only the fixtures can parse."""
    cfg = load(Path.cwd())
    assert cfg.paths_with_role("anchor")
    assert cfg.budgets.handoff_lines > 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_agents_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.agents_config'`

- [ ] **Step 3: Write `docs/agents/surfaces.toml`**

Note the surface list is **today's truth** — `CLAUDE.md`, not `AGENTS.md`. Task 5 changes it.
The four `roles` reproduce exactly the current hardcoded tuples, so Tasks 2–4 are pure
refactors with no behaviour change to argue about.

```toml
# Repo-specific facts that generic tooling and skills read.
#
# This file exists because the doc-surface list had four hardcoded copies — three
# constants in tools/post_branch_checks.py, one in tools/sanity_checks.py, and a
# YAML block inside .claude/skills/post-branch/SKILL.md whose own header says it
# is the only part to edit when porting. Four copies of one list is how the
# workflow skills drifted 89% from the sister fork's while both believed they
# were synced.
#
# Adding a surface here adds it to every consumer at once. That is the point.

[repo]
flip_target = "s10023/buibui-moon-trader-bot"
gh_user = "s10023"

# Consumed by tools/post_branch_checks.py and docs/plans/daily_check.py. The two
# disagreed about nothing only by luck: both spelled 200/600 independently.
[budgets]
handoff_lines = 200
handoff_ceiling = 600
memory_state_bullets = 6
memory_state_ceiling = 8
memory_bytes_cap = 17408

# roles:
#   anchor         -> post_branch_checks.ANCHOR_FILES     (dead-section citations)
#   enumerating    -> post_branch_checks.ENUMERATING_DOCS (does a new file appear anywhere?)
#   negative_claim -> post_branch_checks.NEGATIVE_CLAIM_PATHS
#   sanity         -> sanity_checks.SURFACE_FILES         (CI-gating drift sweep)
#
# A surface carrying any role needs a concrete `path`: the checker opens it.
# `path_glob` entries are for the human-facing table only.

[[surface]]
id = "claude_md"
path = "CLAUDE.md"
purpose = "Authoritative project context for Claude Code"
roles = ["anchor", "enumerating", "negative_claim", "sanity"]

[[surface]]
id = "readme"
path = "README.md"
purpose = "User-facing project overview (CLI subcommands, install, quickstart)"
roles = ["anchor", "enumerating", "negative_claim", "sanity"]

[[surface]]
id = "system_overview"
path = "docs/system-overview.md"
purpose = "Architecture narrative"
roles = ["anchor", "sanity"]

[[surface]]
id = "handoff"
path = "docs/plans/next-conversation-prompt.md"
purpose = "Gitignored session state; budgets above apply to it"
roles = ["anchor"]

[[surface]]
id = "deploy_readme"
path = "deploy/README.md"
purpose = "Install, retention and restore for the VPS deploy kit"
roles = ["enumerating"]

[[surface]]
id = "makefile"
path = "Makefile"
purpose = "Every buibui.py subcommand should have a buibui-* wrapper"
roles = ["negative_claim"]

[[surface]]
id = "docker_compose"
path = "docker-compose.yml"
purpose = "Long-running services and one-shot tools"
roles = ["negative_claim"]

[[surface]]
id = "context_docs"
path_glob = ".claude/context/*.md"
purpose = "Long-form module references (analytics, research-sleeves, tools, signals, web, execution)"

[[surface]]
id = "skill_docs"
path_glob = ".claude/skills/*/SKILL.md"
purpose = "Workflow instructions naming tools, flags and paths — they drift like CLAUDE.md does"
```

- [ ] **Step 4: Write `tools/agents_config.py`**

```python
"""Repo-specific facts that generic tooling reads.

``tomllib`` is stdlib, and that is load-bearing rather than incidental: the
checkers run in CI's dependency-free ``markdownlint`` job, so a loader that
raised ``ImportError`` there would turn every leg into a SKIP — and a SKIP
prints ``0 findings, exit 0``, indistinguishable from a correct run. This repo
has shipped that exact confusion twice.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG = Path("docs/agents/surfaces.toml")


class ConfigError(RuntimeError):
    """The config is absent, malformed or incomplete.

    Deliberately fatal. Every caller renders this as a FINDING; none may
    degrade it to a SKIP or a soft default, because the whole value of a
    single source is that its absence is loud.
    """


@dataclass(frozen=True)
class Budgets:
    handoff_lines: int
    handoff_ceiling: int
    memory_state_bullets: int
    memory_state_ceiling: int
    memory_bytes_cap: int


@dataclass(frozen=True)
class Surface:
    id: str
    purpose: str
    path: str = ""
    path_glob: str = ""
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentsConfig:
    flip_target: str
    gh_user: str
    budgets: Budgets
    surfaces: tuple[Surface, ...]

    def paths_with_role(self, role: str) -> tuple[str, ...]:
        """Every surface path carrying ``role``, in declaration order.

        Order is kept so the config reads as the list a human would write; no
        consumer depends on it.
        """
        return tuple(s.path for s in self.surfaces if role in s.roles)


def _surface(raw: dict[str, Any]) -> Surface:
    try:
        surface = Surface(
            id=str(raw["id"]),
            purpose=str(raw["purpose"]),
            path=str(raw.get("path", "")),
            path_glob=str(raw.get("path_glob", "")),
            roles=tuple(str(r) for r in raw.get("roles", ())),
        )
    except KeyError as exc:
        raise ConfigError(
            f"{CONFIG}: surface is missing a required key: {exc}"
        ) from exc
    if surface.roles and not surface.path:
        raise ConfigError(
            f"{CONFIG}: surface {surface.id!r} carries roles but no concrete path — "
            "a role feeds a checker that opens the file, so a glob reads as missing"
        )
    return surface


def load(root: Path | None = None) -> AgentsConfig:
    """Read the repo config. Raises :class:`ConfigError` rather than defaulting."""
    path = (root or Path.cwd()) / CONFIG
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(
            f"{CONFIG} is missing — the checkers have no surface list"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{CONFIG} is malformed: {exc}") from exc

    try:
        repo = raw["repo"]
        budgets = Budgets(**raw["budgets"])
        surfaces = tuple(_surface(s) for s in raw["surface"])
        return AgentsConfig(
            flip_target=str(repo["flip_target"]),
            gh_user=str(repo["gh_user"]),
            budgets=budgets,
            surfaces=surfaces,
        )
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"{CONFIG} is missing a required key: {exc}") from exc
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_agents_config.py -q`
Expected: PASS, 8 tests.

- [ ] **Step 6: Run the fast gates**

Run: `make lint-py && make typecheck`
Expected: both clean. If `ruff format` rewrites this plan's python fences, commit that
reformat with the task.

- [ ] **Step 7: Commit**

```bash
git add docs/agents/surfaces.toml tools/agents_config.py tests/test_agents_config.py
git commit -m "feat: add docs/agents/surfaces.toml and its loader"
```

---

### Task 2: Repoint `post_branch_checks`' three constants

**Files:**

- Modify: `tools/post_branch_checks.py:61-100` (`ANCHOR_FILES`, `ENUMERATING_DOCS`, `NEGATIVE_CLAIM_PATHS`)
- Test: `tests/test_post_branch_checks.py`

**Interfaces:**

- Consumes: `tools.agents_config.load`, `AgentsConfig.paths_with_role`.
- Produces: `anchor_files()`, `enumerating_docs()`, `negative_claim_paths()`, each
  `-> tuple[str, ...]`; `_cfg() -> AgentsConfig` (lru_cached).

⚠ **These are functions, not module-level constants, and that is load-bearing.** A
module-level `_CFG = load()` would make a missing config crash on *import* — so
`post_branch_checks` would die with a traceback instead of reporting a finding, and it would
take the CI-gating `sanity_checks` down with it. The spec's rule is FINDING, never SKIP and
never crash. Deferring the read to call time is what lets `gather()` catch `ConfigError` and
render it as one. The three call sites at lines 409, 461 and 658 gain `()`.

This is a **behaviour-identical refactor**: the config reproduces the current tuples exactly.
If any check changes its output, the config is wrong, not the code.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_post_branch_checks.py`:

```python
class TestSurfaceListsComeFromConfig:
    """The mutation control for the repoint.

    Asserting the tuples merely *contain* the right paths passes just as well
    against a surviving hardcoded copy. These assert they are DERIVED.
    """

    def test_anchor_files_matches_the_config_view(self) -> None:
        from tools import agents_config, post_branch_checks

        cfg = agents_config.load(Path.cwd())
        assert post_branch_checks.anchor_files() == cfg.paths_with_role("anchor")

    def test_enumerating_docs_matches_the_config_view(self) -> None:
        from tools import agents_config, post_branch_checks

        cfg = agents_config.load(Path.cwd())
        assert post_branch_checks.enumerating_docs() == cfg.paths_with_role(
            "enumerating"
        )

    def test_negative_claim_paths_matches_the_config_view(self) -> None:
        from tools import agents_config, post_branch_checks

        cfg = agents_config.load(Path.cwd())
        assert post_branch_checks.negative_claim_paths() == cfg.paths_with_role(
            "negative_claim"
        )

    def test_a_config_failure_renders_as_a_finding_not_a_crash(self) -> None:
        """The whole reason the read is deferred to call time.

        An import-time load would crash the runner, and a swallowed one would
        print `0 findings, exit 0` — the SKIP-looks-like-PASS failure this repo
        has shipped twice. It must be neither.
        """
        from tools import post_branch_checks
        from tools.agents_config import ConfigError

        def boom() -> None:
            raise ConfigError("docs/agents/surfaces.toml is missing")

        results = post_branch_checks.gather(_runner_stub, load_config=boom)
        config_leg = [r for r in results if r.name == "agents-config"]
        assert len(config_leg) == 1
        assert config_leg[0].findings, "a missing config must FIRE, not skip"
        assert config_leg[0].skipped is None

    def test_the_view_is_not_a_hardcoded_copy(self, tmp_path: Path) -> None:
        """Drop a role from a fixture config; the derived tuple must shrink.

        A module that kept its literal tuple passes the three tests above and
        fails this one.
        """
        from tools import agents_config

        (tmp_path / "docs" / "agents").mkdir(parents=True)
        real = (Path.cwd() / agents_config.CONFIG).read_text(encoding="utf-8")
        (tmp_path / agents_config.CONFIG).write_text(
            real.replace('"anchor", ', "", 1), encoding="utf-8"
        )
        shrunk = agents_config.load(tmp_path)
        assert len(shrunk.paths_with_role("anchor")) < len(
            agents_config.load(Path.cwd()).paths_with_role("anchor")
        )
```

Add `from pathlib import Path` to the test file's imports if absent. `_runner_stub` is a
`Runner` returning `""` for any argv — the file already defines stub runners in this shape;
reuse one rather than adding another.

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/test_post_branch_checks.py -k SurfaceListsComeFromConfig -q`
Expected: FAIL — `AttributeError: module 'tools.post_branch_checks' has no attribute
'anchor_files'`.

- [ ] **Step 3: Replace the three literals**

In `tools/post_branch_checks.py`, add to the imports:

```python
from functools import lru_cache

from tools.agents_config import AgentsConfig, ConfigError
from tools.agents_config import load as load_agents_config
```

Replace the three literal tuples with deferred accessors, keeping every explanatory comment
above them — they record *why* each list is scoped as it is, which the config cannot carry:

```python
@lru_cache(maxsize=1)
def _cfg() -> AgentsConfig:
    return load_agents_config()


def anchor_files() -> tuple[str, ...]:
    return _cfg().paths_with_role("anchor")


def enumerating_docs() -> tuple[str, ...]:
    return _cfg().paths_with_role("enumerating")


def negative_claim_paths() -> tuple[str, ...]:
    return _cfg().paths_with_role("negative_claim")
```

Update the three call sites to call them: line 409 `*negative_claim_paths()`, line 461
`_read_all(enumerating_docs())`, line 658 `[Path(f) for f in anchor_files() if ...]`.

Then give `gather()` an injectable config read, so a broken config becomes a finding rather
than a traceback:

```python
def gather(
    runner: Runner = _run,
    load_config: Callable[[], object] = _cfg,
) -> list[CheckResult]:
    try:
        load_config()
    except ConfigError as exc:
        return [CheckResult("agents-config", [Finding("agents-config", str(exc))])]
    ...
```

Returning early is deliberate: with no surface list, every downstream leg would sweep nothing
and report clean, which is the false all-clear the early return exists to prevent.

Leave `ANCHOR_ROOTS`, `CONTEXT_DOCS` and `SHARED_CONSTANT_BASENAMES` alone — they are not
surface lists and have one consumer each.

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/test_post_branch_checks.py -q`
Expected: PASS, including the pre-existing tests — no behaviour changed.

- [ ] **Step 5: Confirm the sweep still RUNS rather than skipping**

Run: `make post-branch-checks`
Expected: the same legs listed as before, none reporting SKIPPED for a config reason.
⚠ A SKIP here looks exactly like a clean run; read the leg names, not the exit code.

- [ ] **Step 6: Commit**

```bash
git add tools/post_branch_checks.py tests/test_post_branch_checks.py
git commit -m "refactor: read post-branch surface lists from docs/agents/surfaces.toml"
```

---

### Task 3: Repoint `sanity_checks.SURFACE_FILES`

**Files:**

- Modify: `tools/sanity_checks.py:54`
- Test: `tests/test_sanity_checks.py`

**Interfaces:**

- Consumes: `tools.agents_config.load`, `paths_with_role("sanity")`.
- Produces: `SURFACE_FILES: tuple[str, ...]`, same name and type; consumer at line 210 unchanged.

⚠ This module gates in CI. Its legs degrade to SKIPPED when project imports are unavailable —
so after this change, **confirm once locally that the legs RUN**, per the standing rule.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sanity_checks.py`:

Ensure the file imports `pytest` and `from pathlib import Path` at module level — the second
test annotates `tmp_path: Path`, so a function-local import will not satisfy mypy.

```python
class TestSurfaceFilesComeFromConfig:
    def test_surface_files_matches_the_config_view(self) -> None:
        from tools import agents_config, sanity_checks

        cfg = agents_config.load(Path.cwd())
        assert sanity_checks.surface_files() == cfg.paths_with_role("sanity")

    def test_config_failure_is_not_swallowed(self, tmp_path: Path) -> None:
        """A missing config must raise, never yield an empty sweep.

        An empty SURFACE_FILES makes the drift sweep vacuously clean — the
        failure this repo has shipped twice.
        """
        from tools import agents_config

        with pytest.raises(agents_config.ConfigError):
            agents_config.load(tmp_path)
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/test_sanity_checks.py -k SurfaceFilesComeFromConfig -q`
Expected: FAIL — `module 'tools.sanity_checks' has no attribute 'surface_files'`.

- [ ] **Step 3: Replace the literal**

```python
from functools import lru_cache

from tools.agents_config import load as load_agents_config

SURFACE_ROOTS = (".claude",)


@lru_cache(maxsize=1)
def surface_files() -> tuple[str, ...]:
    return load_agents_config().paths_with_role("sanity")
```

Deferred for the same reason as Task 2: this module gates in CI, and an import-time raise
would replace a readable finding with a traceback. Update the consumer at line 210 to
`paths += [Path(p) for p in surface_files()]`.

Keep the existing comment above `SURFACE_ROOTS` verbatim — it records why the scope is wider
than `.claude/`, which is the reason `docs/system-overview.md` is in the list at all.

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/test_sanity_checks.py -q`
Expected: PASS.

- [ ] **Step 5: Confirm the CI-gating legs actually run**

Run: `make sanity-checks`
Expected: the 7 legs listed, and the ones needing project imports report RUN, not SKIPPED.
Record which branch you saw — this is the step that catches a broken import masquerading as a
correct CI run.

- [ ] **Step 6: Commit**

```bash
git add tools/sanity_checks.py tests/test_sanity_checks.py
git commit -m "refactor: read the sanity-check surface list from docs/agents/surfaces.toml"
```

---

### Task 4: Rebuild the `handoff-size` leg and the memory budgets

**Files:**

- Modify: `tools/post_branch_checks.py:675-688` (`_check_handoff_size`), `:629-645` (`_check_memory_cap`)
- Test: `tests/test_post_branch_checks.py`

**Interfaces:**

- Consumes: `AgentsConfig.budgets`.
- Produces: `_check_handoff_size(handoff: str, budgets: Budgets | None = None) -> list[Finding]`
  and `_check_memory_cap(budgets: Budgets | None = None) -> list[Finding]`. The optional
  parameter exists so tests can drive thresholds without writing a config; production callers
  in `gather()` pass nothing.

**Why this is a behaviour change and the previous tasks were not.** The current leg compares a
`Line count: **N**` stamp against the real count. The stamp was deleted from the handoff and the
leg was not, so `re.search` misses and it returns `[]` — vacuously green on every input, forever,
with no test covering it. The cross-repo standard predicted this in writing.

⚠ **Do not add a "grew this branch" delta check.** The handoff is gitignored, so no baseline
exists to diff against.

- [ ] **Step 1: Write the failing tests**

```python
class TestHandoffSize:
    """The leg this replaces returned [] on every input, and nothing tested it.

    A test asserting only "a small handoff is clean" would reproduce that
    defect exactly, so both firing cases come first.
    """

    def _budgets(self) -> Budgets:
        return Budgets(
            handoff_lines=10,
            handoff_ceiling=20,
            memory_state_bullets=6,
            memory_state_ceiling=8,
            memory_bytes_cap=17408,
        )

    def test_within_budget_is_clean(self) -> None:
        from tools.post_branch_checks import _check_handoff_size

        assert _check_handoff_size("x\n" * 5, self._budgets()) == []

    def test_over_budget_fires(self) -> None:
        from tools.post_branch_checks import _check_handoff_size

        found = _check_handoff_size("x\n" * 15, self._budgets())
        assert len(found) == 1
        assert "15 lines" in found[0].detail
        assert "budget 10" in found[0].detail

    def test_at_ceiling_fires_harder(self) -> None:
        from tools.post_branch_checks import _check_handoff_size

        found = _check_handoff_size("x\n" * 25, self._budgets())
        assert len(found) == 1
        assert "ceiling" in found[0].detail

    def test_empty_handoff_is_not_silently_clean(self) -> None:
        """An absent handoff is a finding: sessions get deleted without it."""
        from tools.post_branch_checks import _check_handoff_size

        found = _check_handoff_size("", self._budgets())
        assert len(found) == 1
        assert "absent" in found[0].detail

    def test_a_stamp_is_no_longer_consulted(self) -> None:
        """The old leg keyed on this line. A file carrying a wrong stamp but a
        fine size must now be clean — otherwise the stamp mechanism survived."""
        from tools.post_branch_checks import _check_handoff_size

        body = "Line count: **999**\n" + "x\n" * 4
        assert _check_handoff_size(body, self._budgets()) == []


class TestMemoryCapUsesConfiguredBudgets:
    def test_bullet_cap_comes_from_config(self) -> None:
        from tools.agents_config import Budgets

        b = Budgets(
            handoff_lines=200,
            handoff_ceiling=600,
            memory_state_bullets=2,
            memory_state_ceiling=3,
            memory_bytes_cap=17408,
        )
        assert b.memory_state_bullets == 2
```

Add `from tools.agents_config import Budgets` to the test file's module-level imports.

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/test_post_branch_checks.py -k "HandoffSize or MemoryCap" -q`
Expected: FAIL — `_check_handoff_size()` takes 1 positional argument.

- [ ] **Step 3: Rewrite both legs**

```python
def _check_handoff_size(handoff: str, budgets: Budgets | None = None) -> list[Finding]:
    """Size the handoff against the configured budget.

    The previous implementation compared a ``Line count: **N**`` stamp against
    the real count and carried no threshold at all. The stamp was later deleted
    without deleting this leg, so it returned ``[]`` on every input — a check
    that cannot fire is dismissal, silently. The budget now comes from
    ``docs/agents/surfaces.toml``, shared with the daily check's ratchet, so the
    two cannot disagree about the number.
    """
    b = budgets or _cfg().budgets
    if not handoff:
        return [
            Finding(
                "handoff-size",
                "handoff is absent — rewrite it; sessions get deleted without it",
            )
        ]
    lines = len(handoff.splitlines())
    if lines >= b.handoff_ceiling:
        return [
            Finding(
                "handoff-size",
                f"{lines} lines, at/past the {b.handoff_ceiling} regression ceiling "
                f"(budget {b.handoff_lines}) — MOVE a block to a memory topic file",
            )
        ]
    if lines > b.handoff_lines:
        return [
            Finding(
                "handoff-size",
                f"{lines} lines vs a budget {b.handoff_lines} "
                f"(red at {b.handoff_ceiling}) — move a standing block out",
            )
        ]
    return []


def _check_memory_cap(budgets: Budgets | None = None) -> list[Finding]:
    b = budgets or _cfg().budgets
    if not MEMORY.exists():
        return []
    text = MEMORY.read_text(encoding="utf-8")
    n = current_state_bullets(text)
    size = len(text.encode("utf-8"))
    findings = []
    if n > b.memory_state_bullets:
        findings.append(
            Finding(
                "memory-cap",
                f"Current State has {n} bullets (cap {b.memory_state_bullets}) — roll one",
            )
        )
    if size > b.memory_bytes_cap:
        findings.append(
            Finding(
                "memory-cap",
                f"MEMORY.md is {size:,} bytes (cap {b.memory_bytes_cap:,})",
            )
        )
    return findings
```

Add `Budgets` to the `agents_config` import line. `gather()` must now pass nothing to either
leg, so both keep working from `_cfg()`. Leave the `re` import alone — several other checks
still use it; only this leg's `re.search` call goes.

- [ ] **Step 4: Run to verify they pass**

Run: `poetry run pytest tests/test_post_branch_checks.py -q`
Expected: PASS.

- [ ] **Step 5: See it fire on the real handoff**

Run: `make post-branch-checks`
Expected: `handoff-size` now reports a real finding — the handoff is 441 lines against a 200
budget. **That is the leg working for the first time**, not a regression.

- [ ] **Step 6: Commit**

```bash
git add tools/post_branch_checks.py tests/test_post_branch_checks.py
git commit -m "fix: give handoff-size a real threshold instead of a deleted stamp"
```

---

### Task 5: The split

**Files:**

- Create: `AGENTS.md`
- Modify: `CLAUDE.md` (reduced to the import plus the harness-only residue)
- Modify: `docs/agents/surfaces.toml` (the `claude_md` surface becomes `agents_md`)

**Interfaces:**

- Consumes: everything from Tasks 1–4; the checkers are already config-driven, so this task
  changes **one config line** rather than five files.
- Produces: `AGENTS.md` at repo root, loaded via `@AGENTS.md` from `CLAUDE.md`.

**Settled by measurement (spec §1):** Claude Code auto-discovers `CLAUDE.md` and
`CLAUDE.local.md` only. A root `AGENTS.md` is never auto-loaded, so the `@AGENTS.md` import is
**required** — without it the file is invisible. There is no double-load.

- [ ] **Step 1: Record the pre-split baseline**

```bash
git show HEAD:CLAUDE.md > /tmp/claude-md-before.md
grep -c '' /tmp/claude-md-before.md
```

Expected: 935 lines. Keep this file; Step 5 verifies against it.

- [ ] **Step 2: Create `AGENTS.md` with the agent-neutral body**

Move these top-level sections out of `CLAUDE.md`, in their existing order and **verbatim**:
`## Project Overview` · `## Key Commands` · `## CLI` (with all four subsections) ·
`## Project Structure` (with all five subsections) · `## Code Style` · `## Testing` ·
`## Dependencies` · `## Documentation` · `## Git Conventions` (with both subsections).

From `## Working Agreement`, move the **Persona**, **Definition of Done**, the
background/foreground paragraph with its hard constraint, and **Anti-drift**.

Give the file this H1 and lede, and nothing else that is new:

```markdown
# AGENTS.md

Instructions for coding agents in this repository. `CLAUDE.md` imports this file, so every
agent reads the same thing. Add repo-wide instructions **here, not there**; keep `CLAUDE.md`
for guidance that only applies to Claude Code's own harness.
```

- [ ] **Step 3: Reduce `CLAUDE.md` to the import plus the residue**

```markdown
@AGENTS.md

# Claude Code

`AGENTS.md` holds this repo's instructions and is imported above. This file keeps only what is
specific to Claude Code's harness — its tools, its model tiers, its hooks, its skills and its
memory tree.
```

Followed by, verbatim from the original: **Token efficiency** and **Model delegation** and
**Guardrail** and the footgun-delivery paragraph (all from `## Working Agreement`), then
`## Session Memory Protocol`, `## Agent Skills` and its `### Subagent definitions` subsection.

- [ ] **Step 4: Point the config at the new surface**

In `docs/agents/surfaces.toml`, replace the `claude_md` entry with:

```toml
[[surface]]
id = "agents_md"
path = "AGENTS.md"
purpose = "Agent-neutral project context: structure, commands, verdicts, conventions"
roles = ["anchor", "enumerating", "negative_claim", "sanity"]

[[surface]]
id = "claude_md"
path = "CLAUDE.md"
purpose = "Claude Code harness specifics: skills, subagents, hooks, memory protocol"
roles = ["anchor", "enumerating", "negative_claim", "sanity"]
```

Both carry the roles: the checkers must sweep each, since each now holds rules.

- [ ] **Step 5: Verify the move changed no prose**

This is the acceptance test for the whole task — a permutation check, not a judgement call.

```bash
# Every non-blank line of the original must survive across the two new files.
sort -u /tmp/claude-md-before.md | grep -v '^[[:space:]]*$' > /tmp/before-lines.txt
cat AGENTS.md CLAUDE.md | sort -u | grep -v '^[[:space:]]*$' > /tmp/after-lines.txt
diff /tmp/before-lines.txt /tmp/after-lines.txt
```

Expected: the only differences are **additions** — the two new H1s, the two new lede
paragraphs, and the `@AGENTS.md` line. **Any deletion is a dropped rule; stop and restore it.**

- [ ] **Step 6: Verify the import actually loads**

Run `/context` in a fresh session in this repo and confirm both `CLAUDE.md` and `AGENTS.md`
appear under **Memory files**. If `AGENTS.md` is missing, the import line is inside a code fence
or backticks — import parsing skips both.

- [ ] **Step 7: Run the gates**

Run: `make lint-md && make post-branch-checks && make sanity-checks`
Expected: markdownlint clean; the surface checks now sweep `AGENTS.md` with no new findings
beyond the known `handoff-size` one from Task 4.

- [ ] **Step 8: Commit**

```bash
git add AGENTS.md CLAUDE.md docs/agents/surfaces.toml
git commit -m "refactor: split CLAUDE.md into AGENTS.md plus a Claude-only residue"
```

---

### Task 6: The remaining consumers and prose pointers

**Files:**

- Modify: `docs/plans/daily_check.py:942` (gitignored)
- Modify: `.claude/skills/post-branch/SKILL.md:99-140` (the `surfaces:` block)
- Modify: `README.md:130`
- Modify: `.claude/context/analytics.md`, `execution.md`, `research-sleeves.md`, `signals.md`, `tools.md`, `web.md` (the "Moved out of CLAUDE.md" headers)

**Interfaces:**

- Consumes: `AgentsConfig.budgets`.
- Produces: nothing new.

- [ ] **Step 1: Point `daily_check.py` at the config**

Replace the literal with a read, keeping the long comment block above it — it argues *why* the
thresholds are two-stage, which the config cannot carry:

```python
from tools.agents_config import load as load_agents_config

_B = load_agents_config().budgets
_HANDOFF_BUDGET, _HANDOFF_CEIL = _B.handoff_lines, _B.handoff_ceiling
_STATE_BUDGET, _STATE_CEIL = _B.memory_state_bullets, _B.memory_state_ceiling
```

- [ ] **Step 2: Exercise it by hand**

Run: `PYTHONPATH=. poetry run python docs/plans/daily_check.py`
Expected: the `handoff size` line still reads `441 lines vs a 200 budget (red at 600)`.
⚠ **This file is gitignored, so no CI job can catch it breaking**, and its own `except` would
swallow a failure into an `i` line reading `check failed`. Read the line, do not trust the exit
code.

- [ ] **Step 3: Replace the skill's `surfaces:` block with a pointer**

In `.claude/skills/post-branch/SKILL.md`, delete the `yaml` block at lines 99–140 and put in
its place:

```markdown
The doc-surface list lives in `docs/agents/surfaces.toml`, not here. **When porting this skill
to another repo, that file is the only thing to write** — the workflow below is repo-agnostic.
`tools/post_branch_checks.py` and `tools/sanity_checks.py` read the same file, so the list the
skill describes and the list the checkers sweep cannot drift apart.
```

- [ ] **Step 4: Update the prose pointers**

- `README.md:130` — `` `CLAUDE.md` carries the package index and the sleeve verdicts. `` becomes
  `` `AGENTS.md` carries the package index and the sleeve verdicts. ``
⚠ **Do not touch `deploy/README.md:393`.** It names `~/.claude-personal/CLAUDE.md`, the
*account-level* file restored from a backup snapshot. It is a different file in a different
tree and this split does not rename it. A blind grep-and-replace for `CLAUDE.md` breaks the
documented restore procedure.

- In each of the six `.claude/context/*.md` files, the header sentence "Moved out of CLAUDE.md
  on 2026-08-04 … **CLAUDE.md keeps the package index and the** … update CLAUDE.md too" becomes
  the same sentence naming `AGENTS.md`. ⚠ Keep the **date** as 2026-08-04: it records when the
  move happened, not when the file was renamed.

- [ ] **Step 5: Run the full gates**

Run: `make lint-py && make typecheck && make lint-md`, then `make test` **in the background**
(~145s), and touch nothing under the Python tree until it returns.
Expected: all green. `make test-regression` is not required — no backtest-surface path changed.

- [ ] **Step 6: Commit**

```bash
git add docs/plans/daily_check.py .claude/skills/post-branch/SKILL.md README.md .claude/context
git commit -m "refactor: point the remaining consumers and prose at AGENTS.md and the config"
```

---

## After the last task

- [ ] Run `/post-branch` phases 0–4 **before** `gh pr create` — the doc walk must ship in the
      initial push, or every fix re-runs the whole CI matrix on an open PR.
- [ ] The `handoff-size` finding from Task 4 is real: prune the handoff to budget as part of
      the sweep, by **moving** blocks to memory topic files.
- [ ] Confirm the visibility flip with the operator before `gh pr create`. Mechanics are
      standing authorisation; timing is per-occasion consent.
- [ ] Update SoT ST44: items (1) and (2) close, leaving (4) and (8) as dated NOT-NOWs.
