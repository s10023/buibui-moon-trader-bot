"""Run every mechanical check `/post-branch` used to carry as copy-by-hand bash.

The skill embedded 16 shell blocks in prose. A session had to notice each one,
copy it, and run it — which is the failure AGENTS.md names as *a hand walk is
not the walk*, and it is why the same defects kept recurring: prose cannot
enforce. Every check that can run without judgement lives here instead, so the
skill's instruction is "run this, triage the hits" rather than sixteen
invitations to remember.

Two checks are new, and both fix defects the prose form could not:

* ``queue-items`` — nothing swept the handoff's own task list for work the
  branch just did, so an item survived as a confident instruction to redo
  finished work. Confirmed three times. The prose mitigation keyed on added
  Python *symbols*, which a docs-only branch does not have; this keys the
  handoff's own distinctive tokens against the diff **content**, so it sees a
  branch that adds no code at all.
* ``new-files`` — the prose probed ``basename``, and every skill's basename is
  the shared constant ``SKILL.md``, which matches CLAUDE.md's generic sentence
  about where skills live. A fabricated skill therefore reported COVERED, the
  exact false-positive the check's own ``-w`` rule exists to prevent. When a
  basename carries no identity, :func:`probe_names` probes the parent directory
  instead.

Checks are pure functions over text wherever possible; the git surface is
injected as ``runner`` so the suite can exercise them without a repository.
"""

from __future__ import annotations

import argparse
import re
import subprocess  # noqa: S404 - git plumbing, fixed argv, no shell
import sys
import textwrap
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# Runnable as a bare script, not only through the Make target. CI invokes this as
# `python3 tools/post_branch_checks.py` with no PYTHONPATH, which puts `tools/` on
# sys.path rather than the repo root — so the `tools.*` imports below would raise
# ModuleNotFoundError and the step would fail on an import, not on a finding.
# The Make target sets PYTHONPATH=., so a green local run cannot catch that.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.agents_config import AgentsConfig, Budgets, ConfigError  # noqa: E402
from tools.agents_config import load as load_agents_config  # noqa: E402
from tools.memory_dir import memory_dir  # noqa: E402
from tools.stale_anchors import default_resolver, describe, scan  # noqa: E402

Runner = Callable[[Sequence[str]], str]

HANDOFF = Path("docs/plans/next-conversation-prompt.md")

#: Names that must never enter a tracked file — employer, clients, work repos.
#: **Gitignored on purpose: a tracked list of the words you are hiding is the
#: leak it exists to prevent.** It therefore dies on a reclone, like the hooks,
#: which is why an absent list is a FINDING rather than a SKIP.
SENSITIVE_TERMS = Path(".claude/sensitive-terms.txt")
#: ⚠ RESOLVED, never hardcoded. This was a literal `~/.claude-personal/...` path until
#: 2026-09-19, so after the Windows migration moved the tree to `~/.claude/projects/`
#: the `memory-cap` leg read a file that does not exist and reported **clean** — the
#: SKIP-wearing-a-PASS shape `sanity_checks.py` documents, arriving here by the same
#: route it arrived in `daily_check.py` (#772, which fixed that file and only that file).
MEMORY_DIR = memory_dir()
MEMORY = MEMORY_DIR / "MEMORY.md"


@lru_cache(maxsize=1)
def _cfg() -> AgentsConfig:
    return load_agents_config()


#: Current-state doc surfaces swept for dead anchor citations. Deliberately the
#: same shape as `sanity_checks.SURFACE_ROOTS` — the dated trees are excluded by
#: `stale_anchors.is_dated_path`, because a citation in a dated record was
#: correct when written. Hand-sweeping this class found 2 such correct
#: citations against 4 live ones, so the exclusion is load-bearing.
ANCHOR_ROOTS = (".claude",)


def anchor_files() -> tuple[str, ...]:
    return _cfg().paths_with_role("anchor")


#: Basenames that identify a *role* rather than a file. Probing these by name
#: matches unrelated prose, so the parent directory is the real identity.
SHARED_CONSTANT_BASENAMES = frozenset(
    {"SKILL.md", "README.md", "__init__.py", "INDEX.md", "index.ts", "main.py"}
)


#: Docs that enumerate files by name. A new operator-facing file should appear
#: in at least one of them. The Makefile is deliberately absent: a build rule is
#: not documentation, and including it would let a file mentioned in no prose
#: report COVERED.
def enumerating_docs() -> tuple[str, ...]:
    return _cfg().paths_with_role("enumerating")


CONTEXT_DOCS = (".claude/context",)

#: Absence-language. Kept narrow on purpose — generic phrases ("for now",
#: "unwired", "stop-gap") each pulled double-digit false positives for no catch.
NEGATIVE_CLAIM_RE = re.compile(
    r"(never|not) (yet )?ported"
    r"|no (reader|host|consumer)\b"
    r"|this repo has no"
    r"|until (that|the) port lands"
    r"|silent accumulator"
    r"|accumulates? unscored"
    r"|is not (yet )?(available|implemented|wired)",
    re.IGNORECASE,
)


def negative_claim_paths() -> tuple[str, ...]:
    return _cfg().paths_with_role("negative_claim")


#: Tokens that scope a claim line IN while carrying no claim of their own, keyed
#: on ``(path, token)`` with the reason inline — the
#: ``sanity_checks.MISSING_PATH_EXEMPT`` shape.
#:
#: ⚠ **Keyed on the PAIR, never on either half.** A path-wide mute silences a
#: whole document, and a token-wide one carries the hole into every other one.
#: A hit is dropped only when EVERY matched token is exempt, so one unexempt
#: token still reports the line: an entry NARROWS a finding rather than deleting
#: it, which is what stops the allowlist growing into the wide token filter it
#: exists instead of.
#:
#: ⚠ **The tempting fix is the wrong one.** These are 3-12 KB paragraphs, so
#: scoping on a window around the regex match rather than the whole line looks
#: like the real remedy. The fork measured that window against its own tree: it
#: would have suppressed the leg's ONLY true positive on record, whose falsified
#: sentence sat ~1,400 characters from the match on the same line. The finding's
#: value is a human re-reading the paragraph, so the line stays the unit.
#:
#: ⚠ **Calibrated by RUNNING the check, never by hand** — the
#: ``sanity_checks.CONTEXT_EXEMPT`` rule. Every entry names a measurement.
NEGATIVE_CLAIM_EXEMPT: dict[tuple[str, str], str] = {
    (".claude/context/signals.md", "portfolio"): (
        "measured 2026-08-24: the token appears on that 12,659-char line only as "
        "`portfolio.sizing.*` module citations, ~400 chars after a claim about "
        "the CARD's pundit avg_r strip. It is scoped in by any branch adding a "
        "Make target, since `.PHONY` carries `buibui-portfolio-replay` — a false "
        "positive by construction rather than by luck"
    ),
}

_BACKTICKED = re.compile(r"`([^`\n]+)`")
_HYPOTHESIS = re.compile(r"\bH-\d{3}\b")
_MD_NUMBERED_ITEM = re.compile(r"^(\d+)\.\s+(.*)$")
_DEF_OR_CLASS = re.compile(
    r"^[+-]\s*(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE
)
_BAD_ATX = re.compile(r"^#[0-9]")

#: Tokens too common to discriminate — they appear in prose for unrelated
#: reasons and would make every run noisy.
_STOPWORDS = frozenset(
    {
        "main",
        "HEAD",
        "true",
        "false",
        "None",
        "make",
        "git",
        "gh",
        "PR",
        "CI",
        "md",
        "py",
        "1d",
        "4h",
        "1wk",
        # Stems of files nearly every branch touches: they discriminate nothing
        # and their hits drown the ones that matter.
        "CLAUDE",
        "SKILL",
        "README",
        "INDEX",
        "tools",
        "docs",
        "tests",
        "config",
    }
)


@dataclass
class Finding:
    """One thing a human must look at. `detail` is printed verbatim."""

    check: str
    detail: str


@dataclass
class CheckResult:
    name: str
    findings: list[Finding] = field(default_factory=list)
    skipped: str | None = None
    #: Context a human may want without it counting as something to triage.
    #: A check that is never clean trains dismissal, so anything the check
    #: cannot tie to this branch belongs here rather than in ``findings``.
    note: str | None = None


def _run_rc(argv: Sequence[str]) -> int:
    """Exit code of a fixed-argv command; 0 if it cannot be launched."""
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, shell=False
            list(argv),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        ).returncode
    except OSError:
        return 0


def _run(argv: Sequence[str]) -> str:
    """Run a fixed-argv command, returning stdout and swallowing failure.

    A check that cannot run must not abort the sweep — the others still carry
    information.

    ⚠ ``encoding="utf-8"`` is REQUIRED, not tidiness. ``text=True`` alone decodes
    through ``locale.getpreferredencoding()``, which is cp1252 on a Windows host — and
    this repo's own docs are full of ``⚠`` and em-dashes, so the very first `git diff`
    of a docs branch raised ``UnicodeDecodeError`` inside subprocess's reader THREAD.
    That is the nastiest part: the exception surfaced far from here, ``stdout`` came
    back as ``None``, and this function returned it — breaking the contract its own
    signature states — so the sweep died several checks later on
    ``'NoneType' object has no attribute 'splitlines'``. Hence both halves: decode
    explicitly, and never hand back a non-string.
    """
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            list(argv),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return ""
    return out.stdout or ""


# ---------------------------------------------------------------- pure helpers


def probe_names(path: str) -> list[str]:
    """Names to grep when asking "is this file documented anywhere?".

    The basename alone is wrong whenever it is a shared constant: every skill is
    ``SKILL.md``, so a probe for it matches CLAUDE.md's generic sentence about
    where skills live and reports COVERED for a file no doc has heard of. In
    that case the identity lives in the directory, so probe that instead.

    Generalises to any file named for its role rather than its content.
    """
    p = Path(path)
    if p.name in SHARED_CONSTANT_BASENAMES and p.parent.name:
        return [p.parent.name]
    stem = p.stem
    return [p.name] if stem == p.name else [p.name, stem]


def extract_tokens(text: str) -> set[str]:
    """Distinctive tokens from prose: backticked spans plus hypothesis ids.

    Deliberately narrow. A token has to be specific enough that finding it in a
    diff means something; bare English words would match everything.
    """
    out: set[str] = set()
    for raw in _BACKTICKED.findall(text):
        tok = raw.strip()
        # A backticked command line is not a token; take its first word.
        first = tok.split()[0] if tok.split() else ""
        for cand in (tok, first):
            cand = cand.strip("`*_.,:;()[]")
            if len(cand) >= 3 and cand not in _STOPWORDS:
                out.add(cand)
    out.update(_HYPOTHESIS.findall(text))
    return out


def numbered_items(markdown: str) -> dict[int, str]:
    """Split a markdown numbered list into ``{index: full item text}``.

    Continuation lines (indented under the item) belong to the item, which is
    where most of a queue entry's distinctive tokens live.
    """
    items: dict[int, str] = {}
    current: int | None = None
    for line in markdown.splitlines():
        m = _MD_NUMBERED_ITEM.match(line)
        if m:
            current = int(m.group(1))
            items[current] = m.group(2)
        elif current is not None:
            if line.startswith(("   ", "\t")) or (line.strip() and line[0].isspace()):
                items[current] += "\n" + line.strip()
            elif not line.strip():
                continue
            else:
                current = None
    return items


def added_paths(diff_filter_a: str, status_porcelain: str) -> list[str]:
    """Files this branch adds, tracked **and** untracked.

    ``git diff`` in any form cannot see an untracked file, so the presence
    checks reported zero added files on a branch whose new code was not yet
    staged — the precise case they exist to catch. The skill answered this with
    "remember to ``git add -A`` first", which is one more hand-step to forget;
    reading ``git status`` instead makes the check correct either way.
    """
    out = {p for p in diff_filter_a.split() if p}
    for line in status_porcelain.splitlines():
        if line.startswith("?? "):
            path = line[3:].strip()
            if path.endswith("/"):
                continue
            out.add(path)
        elif line[:2] in {"A ", "AM", " A"}:
            out.add(line[3:].strip())
    return sorted(out)


def diff_symbols(diff: str) -> set[str]:
    """Function and class names added or removed in a diff."""
    return set(_DEF_OR_CLASS.findall(diff))


def bad_atx_lines(text: str) -> list[int]:
    """1-indexed lines where a wrapped ``#123`` became an MD018 heading."""
    return [i for i, line in enumerate(text.splitlines(), 1) if _BAD_ATX.match(line)]


def current_state_bullets(memory: str) -> int:
    """Count top-level bullets under MEMORY.md's ``## Current State``."""
    seen = False
    n = 0
    for line in memory.splitlines():
        if line.startswith("## Current State"):
            seen = True
            continue
        if seen:
            if line.startswith("## "):
                break
            if line.startswith("- "):
                n += 1
    return n


# --------------------------------------------------------------------- checks


def check_queue_items(handoff: str, diff: str, diff_names: str) -> list[Finding]:
    """Does this branch CLOSE a task the handoff still lists as to-do?

    Nothing swept for this, so a finished item survived under a heading telling
    the next session it was still owed. The earlier mitigation keyed on added
    Python symbols and therefore could not see a docs-only branch at all; this
    keys the handoff's own tokens against the diff content, which every branch
    has.
    """
    findings: list[Finding] = []
    haystack = diff + "\n" + diff_names
    for idx, body in numbered_items(handoff).items():
        hits = sorted(t for t in extract_tokens(body) if t in haystack)
        if hits:
            head = " ".join(body.split())[:90]
            findings.append(
                Finding(
                    "queue-items",
                    f"item {idx} may be CLOSED by this branch "
                    f"(matched {', '.join(hits[:4])})\n      {head}…",
                )
            )
    return findings


def check_handoff_symbols(handoff: str, diff: str, diff_names: str) -> list[Finding]:
    """Handoff claims naming a symbol or file this branch touched."""
    names = {Path(n).stem for n in diff_names.split() if n} | diff_symbols(diff)
    findings: list[Finding] = []
    for i, line in enumerate(handoff.splitlines(), 1):
        for sym in sorted(names):
            if len(sym) < 4 or sym in _STOPWORDS:
                continue
            if re.search(rf"\b{re.escape(sym)}\b", line):
                findings.append(
                    Finding("handoff-symbols", f"{HANDOFF}:{i} mentions `{sym}`")
                )
                break
    return findings


def check_new_files(added: Sequence[str], doc_blob: str) -> list[Finding]:
    """Every added non-Python operator file should reach an enumerating doc."""
    findings = []
    for path in added:
        if path.startswith(("tests/", "docs/")) or path.endswith(".py"):
            continue
        if not any(
            re.search(rf"\b{re.escape(n)}\b", doc_blob) for n in probe_names(path)
        ):
            findings.append(Finding("new-files", f"UNDOCUMENTED FILE: {path}"))
    return findings


def check_new_modules(added: Sequence[str], context_blob: str) -> list[Finding]:
    """Every added module should reach `.claude/context/`."""
    findings = []
    for path in added:
        if not path.endswith(".py") or path.startswith(("tests/", "docs/")):
            continue
        if not any(
            re.search(rf"\b{re.escape(n)}\b", context_blob) for n in probe_names(path)
        ):
            findings.append(Finding("new-modules", f"UNDOCUMENTED: {path}"))
    return findings


def check_new_targets(diff: str, doc_blob: str) -> list[Finding]:
    """Every added Make target should be documented.

    ``buibui-`` is stripped because AGENTS.md documents the *subcommands* and
    states that each ``buibui-*`` target wraps one; without the strip every
    wrapper reports undocumented, which is noise, and noise gets a check
    ignored.
    """
    findings = []
    for line in diff.splitlines():
        m = re.match(r"^\+([a-z][a-z0-9_-]*):", line)
        if not m:
            continue
        target = m.group(1)
        probes = {target, target.removeprefix("buibui-")}
        if not any(re.search(rf"\b{re.escape(p)}\b", doc_blob) for p in probes):
            findings.append(Finding("new-targets", f"UNDOCUMENTED TARGET: {target}"))
    return findings


#: A target declaration. Deliberately the same shape `check_new_targets` matches,
#: minus the leading `+`, so the two legs cannot disagree about what a target is.
TARGET_DECL_RE = re.compile(r"^([a-z][a-z0-9_.-]*):")


def names_token(text: str, token: str) -> bool:
    """True when ``text`` names ``token`` as a whole word, hyphens included.

    ⚠ **`\\b` is NOT enough for a hyphenated name.** `-` is a non-word character,
    so `\\bbuibui-portfolio-replay\\b` matches happily *inside*
    `buibui-portfolio-replay-extra` — the `-w` trap the `/post-branch` skill
    documents for `_`, arriving from the other side, because `_` is
    word-constituent and `-` is not. Every Make target here is hyphenated, so the
    plain form would credit a doc that names a DIFFERENT target.
    """
    return re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", text) is not None


def changed_line_numbers(diff: str) -> set[int]:
    """New-file line numbers a unified diff touches.

    A **deletion** carries no new-file line number of its own, so it is blamed on
    the position it vacated. Skipping it would make a recipe line *removed* from a
    target invisible to `check_amended_targets` — the same amendment, arriving as
    a subtraction.
    """
    touched: set[int] = set()
    new_ln = 0
    for line in diff.splitlines():
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            new_ln = int(m.group(1)) if m else 0
            continue
        if not new_ln or line.startswith(("+++", "---", "diff ", "index ")):
            continue
        if line.startswith("+"):
            touched.add(new_ln)
            new_ln += 1
        elif line.startswith("-"):
            touched.add(new_ln)
        else:
            new_ln += 1
    return touched


def targets_by_line(makefile_text: str) -> dict[int, str]:
    """Map each 1-based Makefile line to the target whose recipe it sits in.

    A declaration claims its own line and every line after it until something
    un-indented ends the recipe. `.PHONY:` NAMES targets without being one, so it
    fails `TARGET_DECL_RE` and, being un-indented, also clears the current target.
    """
    out: dict[int, str] = {}
    current: str | None = None
    for i, line in enumerate(makefile_text.splitlines(), 1):
        m = TARGET_DECL_RE.match(line)
        if m:
            current = m.group(1)
        elif line and not line[0].isspace():
            current = None
        if current:
            out[i] = current
    return out


def check_amended_targets(
    makefile_diff: str, makefile_text: str, docs: Mapping[str, str]
) -> list[Finding]:
    """Docs enumerating a target whose recipe this branch AMENDED.

    `check_new_targets` asks whether an **added** target is documented. It is
    structurally blind to an existing one that gains an override, changes a
    default or renames a variable — the doc still names the target, so every
    presence check passes while its enumeration goes one short. Measured on #746:
    `buibui-portfolio-replay` gained `DB=` and both `AGENTS.md` and `README.md`
    kept listing three overrides; nothing flagged either.

    Deliberately does NOT parse what changed. Scoping to `$(if $(VAR),...)` would
    scope to the symptom that happened to be noticed, which is the recurring
    defect in this repo's guard design — a changed default has the same shape and
    the same invisible failure. It reports the target and the docs to re-read, and
    leaves the judgement to a human.
    """
    if not makefile_diff.strip():
        return []
    line_map = targets_by_line(makefile_text)
    added = {
        m.group(1)
        for line in makefile_diff.splitlines()
        if line.startswith("+") and (m := TARGET_DECL_RE.match(line[1:]))
    }
    touched = {
        target
        for ln in changed_line_numbers(makefile_diff)
        if (target := line_map.get(ln))
    }
    findings = []
    for target in sorted(touched - added):
        naming = sorted(
            path for path, text in docs.items() if names_token(text, target)
        )
        if naming:
            findings.append(
                Finding(
                    "amended-targets",
                    f"AMENDED: {target} — re-read {', '.join(naming)}",
                )
            )
    return findings


def check_negative_claims(
    runner: Runner, diff: str, diff_names: str
) -> tuple[list[Finding], int, int]:
    """Docs asserting the absence of something THIS branch just added.

    Returns ``(findings, suppressed, exempted)``. The absence corpus is a
    property of the tree, not of the branch, so reporting all of it every run
    made this the one leg that was never clean — and a check that is never clean
    trains dismissal exactly as a check that is never green stops being read.
    The scope is the intersection with the branch, which is what the sentence
    beside it always claimed; both remainders are counted into a note.

    Scoping is on the claim line's own distinctive tokens against the diff's
    ADDED lines. Additions only: a branch that REMOVES the named thing makes
    an absence claim more true, not less.

    ⚠ A claim line with no extractable token cannot be ruled out, so it is
    reported. This leg fails OPEN on purpose — a miss ships a doc denying
    something now present, which is the whole harm the check exists to catch.

    ⚠ ``NEGATIVE_CLAIM_EXEMPT`` suppresses a hit only when EVERY matched token
    is exempt for that path. One unexempt token reports the whole line, so an
    entry narrows a finding rather than deleting it.
    """
    added = "\n".join(line for line in diff.splitlines() if line.startswith("+"))
    haystack = added + "\n" + diff_names
    # ``.`` ENUMERATES the corpus — every non-empty line — because
    # ``NEGATIVE_CLAIM_RE`` below is the only filter this leg has. Passing a
    # content pattern here splits the rule across two spellings, and that is
    # exactly how it shipped broken: the pattern was ``x``, the LETTER, so git
    # dropped every line without one before the regex ever ran. Measured at the
    # fix: 1,757 of 13,615 lines survived (12.9%), matching 1 real claim of 4.
    out = runner(["git", "grep", "-nI", "-e", ".", "--", *negative_claim_paths()])
    findings: list[Finding] = []
    suppressed = 0
    exempted = 0
    for line in out.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3 or "post-branch" in parts[0]:
            continue
        m = NEGATIVE_CLAIM_RE.search(parts[2])
        if not m:
            continue
        tokens = extract_tokens(parts[2])
        hits = sorted(t for t in tokens if t in haystack)
        if tokens and not hits:
            suppressed += 1
            continue
        if hits and all((parts[0], h) in NEGATIVE_CLAIM_EXEMPT for h in hits):
            exempted += 1
            continue
        why = f" (matched {', '.join(hits[:3])})" if hits else " (no token to scope on)"
        findings.append(
            Finding("negative-claims", f"{parts[0]}:{parts[1]}: {m.group(0)}{why}")
        )
    return findings, suppressed, exempted


# ------------------------------------------------------------------ execution


def _negative_claims_result(runner: Runner, diff: str, diff_names: str) -> CheckResult:
    """Wrap the check so both remainders are a note, not a finding.

    The exempt count is printed rather than swallowed: an allowlist nobody can
    see is a mute, and a mute is what this leg's own history argues against.
    """
    findings, suppressed, exempted = check_negative_claims(runner, diff, diff_names)
    parts = []
    if suppressed:
        parts.append(
            f"{suppressed} standing absence claim(s) in the tree are unrelated "
            "to this diff (scoped out, not dismissed)"
        )
    if exempted:
        parts.append(
            f"{exempted} scoped in only by token(s) on NEGATIVE_CLAIM_EXEMPT "
            "(reason inline there)"
        )
    return CheckResult("negative-claims", findings, note="; ".join(parts) or None)


def gather(
    runner: Runner = _run,
    load_config: Callable[[], object] = _cfg,
) -> list[CheckResult]:
    """Run every check against the working tree. Order matches the skill."""
    # Returning early is deliberate: with no surface list, every downstream leg
    # would sweep nothing and report clean, which is the false all-clear this
    # early return exists to prevent.
    try:
        load_config()
    except ConfigError as exc:
        return [CheckResult("agents-config", [Finding("agents-config", str(exc))])]

    diff = runner(["git", "diff", "main", "--"])
    diff_names = runner(["git", "diff", "main", "--name-only"])
    makefile_diff = runner(["git", "diff", "main", "--", "Makefile"])
    added = added_paths(
        runner(["git", "diff", "main", "--diff-filter=A", "--name-only"]),
        runner(["git", "status", "--porcelain"]),
    )
    changed_md = sorted(
        {p for p in diff_names.split() if p.endswith(".md")}
        | {p for p in added if p.endswith(".md")}
    )

    handoff = HANDOFF.read_text(encoding="utf-8") if HANDOFF.exists() else ""
    doc_blob = _read_all(enumerating_docs())
    context_blob = _read_all(CONTEXT_DOCS)
    makefile_text = Path("Makefile").read_text(encoding="utf-8", errors="replace")
    enumerating_by_path = _read_each(enumerating_docs())

    results = [
        CheckResult("queue-items", check_queue_items(handoff, diff, diff_names))
        if handoff
        else CheckResult("queue-items", skipped="no handoff file"),
        CheckResult("handoff-symbols", check_handoff_symbols(handoff, diff, diff_names))
        if handoff
        else CheckResult("handoff-symbols", skipped="no handoff file"),
        CheckResult("new-files", check_new_files(added, doc_blob)),
        CheckResult("new-modules", check_new_modules(added, context_blob)),
        CheckResult("new-targets", check_new_targets(makefile_diff, doc_blob)),
        CheckResult(
            "amended-targets",
            check_amended_targets(makefile_diff, makefile_text, enumerating_by_path),
        ),
        _negative_claims_result(runner, diff, diff_names),
        CheckResult("doc-indexes", _check_doc_indexes()),
        CheckResult("md-atx", _check_md_atx(changed_md)),
        CheckResult("memory-cap", _check_memory_cap()),
        _handoff_size_result(handoff, runner),
        CheckResult("stale-anchors", _check_stale_anchors()),
        sensitive_terms_result(runner),
    ]
    return results


def load_sensitive_terms(text: str) -> list[str]:
    """Non-empty, non-comment lines, lowercased."""
    out = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().lower()
        if line:
            out.append(line)
    return out


def mask_term(term: str) -> str:
    """Enough to identify, not enough to re-state.

    This output is read in a terminal and pasted into handoffs and PR bodies,
    which are themselves tracked or backed up. Printing the term in full would
    reproduce the leak inside the report about the leak — the same shape as the
    ``gh pr create`` hook firing on its own documentation.
    """
    return f"{term[:3]}\u2026" if len(term) > 3 else "\u2026"


def _resolve_terms(terms: Sequence[str] | None) -> list[str]:
    """Injected terms, else the gitignored list, else nothing."""
    if terms is not None:
        return list(terms)
    if not SENSITIVE_TERMS.exists():
        return []
    return load_sensitive_terms(SENSITIVE_TERMS.read_text(encoding="utf-8"))


def _not_configured() -> Finding:
    """Shared by both modes: `--text` must not be the one place this is a pass."""
    return Finding(
        "sensitive-terms",
        f"NOT CONFIGURED — no {SENSITIVE_TERMS}; this gate is not "
        "running, which is NOT the same as passing",
    )


def scan_text_for_terms(label: str, text: str, terms: Sequence[str]) -> list[Finding]:
    """Sensitive terms in a composed text that has not been published yet.

    Line numbers only, never the surrounding text: the match sits inside the
    very prose being screened, so quoting context would reproduce the term the
    masking exists to withhold.
    """
    numbered = list(enumerate(text.splitlines(), 1))
    findings = []
    for term in terms:
        at = [n for n, line in numbered if term in line.lower()]
        if not at:
            continue
        shown = ", ".join(str(n) for n in at[:5]) + (" …" if len(at) > 5 else "")
        findings.append(
            Finding(
                "sensitive-terms",
                f"{mask_term(term)} in {label} at line(s) {shown} — a PR title "
                "or body is PUBLIC the moment it posts, and editing it later "
                "does not unpublish it",
            )
        )
    return findings


def sensitive_text_result(
    texts: Sequence[tuple[str, str]], terms: Sequence[str] | None = None
) -> CheckResult:
    """Screen a composed PR title/body BEFORE `gh pr create` posts it.

    ⚠ **The fourth exposure surface, and the only INDEXABLE one.** The three
    legs of :func:`sensitive_terms_result` ask about the tracked tree and this
    branch's commits; a PR title and body are neither, so that gate reports
    ``clean`` on a body naming every term — correctly, and uselessly. It matters
    more here than in the fork this is ported from: this repo flips PUBLIC to
    run CI, and AGENTS.md calls that pre-flip leg *the* gate.

    Takes ``(label, text)`` pairs because a title and a body post together, so
    screening one without the other is the same gap one level down.
    """
    resolved = _resolve_terms(terms)
    if not resolved:
        return CheckResult("sensitive-terms", [_not_configured()])
    findings = [
        f for label, text in texts for f in scan_text_for_terms(label, text, resolved)
    ]
    note = None
    if not findings:
        note = (
            f"{len(resolved)} term(s) checked against "
            f"{len(texts)} composed text(s); nothing to mask"
        )
    return CheckResult("sensitive-terms", findings, note=note)


def sensitive_terms_result(
    runner: Runner, terms: Sequence[str] | None = None
) -> CheckResult:
    """Pre-flip gate: would making this repo public expose a work identifier?

    Three questions, because they fail differently. The tracked tree answers
    "is it visible now"; the branch's commit CONTENT answers "am I adding one";
    and the commit MESSAGES answer the one that no file edit can ever undo. A
    flip republishes the entire history, so deleting the file later does not
    unexpose the blob — and a message cannot be deleted at all short of a
    rewrite. The message leg exists because this gate's own scrub commit named
    a term in its subject and the first version read clean.

    main's pre-existing occurrences are deliberately NOT re-reported: the
    operator accepted that baseline on 2026-08-19, and a check that is never
    clean trains dismissal.

    ⚠ **The fourth surface is NOT here.** A PR title and body are neither the
    tree nor a commit, so screening them is :func:`sensitive_text_result`
    (``--text``), run before `gh pr create`.
    """
    terms = _resolve_terms(terms)
    if not terms:
        return CheckResult("sensitive-terms", [_not_configured()])

    findings: list[Finding] = []
    for term in terms:
        tracked = runner(["git", "grep", "-il", term, "--", "."]).split()
        if tracked:
            shown = ", ".join(tracked[:3]) + (" …" if len(tracked) > 3 else "")
            findings.append(
                Finding(
                    "sensitive-terms",
                    f"{mask_term(term)} in {len(tracked)} tracked file(s): {shown}",
                )
            )
        messages = runner(["git", "log", "main..HEAD", "--format=%B%n%s"])
        if term in messages.lower():
            findings.append(
                Finding(
                    "sensitive-terms",
                    f"{mask_term(term)} in a commit MESSAGE on this branch — no "
                    "file deletion reaches a message; only a history rewrite does",
                )
            )
        introduced = runner(["git", "log", "--oneline", "main..HEAD", "-S", term])
        if introduced.strip():
            n = len(introduced.strip().splitlines())
            findings.append(
                Finding(
                    "sensitive-terms",
                    f"{mask_term(term)} introduced by {n} commit(s) on this branch "
                    "— a flip republishes the whole history, so scrubbing it in a "
                    "later commit will NOT unexpose it",
                )
            )

    note = None
    if not findings:
        note = (
            f"{len(terms)} term(s) checked against the tracked tree and this "
            "branch's commits; main's accepted historical baseline is not re-reported"
        )
    return CheckResult("sensitive-terms", findings, note=note)


def _read_all(paths: Sequence[str]) -> str:
    chunks: list[str] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            chunks.extend(
                f.read_text(encoding="utf-8", errors="replace")
                for f in sorted(p.rglob("*.md"))
            )
        elif p.exists():
            chunks.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _read_each(paths: Sequence[str]) -> dict[str, str]:
    """`_read_all`, but keyed by path.

    `amended-targets` reports WHICH doc to re-read, which a joined blob cannot
    say. Directories expand the same way, so the two helpers cannot disagree
    about what the enumerating corpus is.
    """
    out: dict[str, str] = {}
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for f in sorted(p.rglob("*.md")):
                out[str(f)] = f.read_text(encoding="utf-8", errors="replace")
        elif p.exists():
            out[str(p)] = p.read_text(encoding="utf-8", errors="replace")
    return out


def _check_doc_indexes() -> list[Finding]:
    """`docs/*/INDEX.md` are GENERATED and a test compares them byte-for-byte.

    A branch that adds, renames or deletes an audit or spec therefore leaves a
    **red suite**, not a lint nit, until the indexes are refreshed. Checked here
    rather than at pre-merge because this is local, offline and sub-second,
    while a failure caught after `gh pr create` costs a second full matrix.
    """
    if not Path("tools/docs_index.py").exists():
        return []
    if _run_rc(["poetry", "run", "python", "tools/docs_index.py", "--check"]) != 0:
        return [
            Finding(
                "doc-indexes",
                "an INDEX.md is stale — run `make docs-index`, never hand-edit it",
            )
        ]
    return []


def _check_md_atx(changed_md: Sequence[str]) -> list[Finding]:
    findings = []
    for raw in changed_md:
        p = Path(raw)
        if not p.exists():
            continue
        for line_no in bad_atx_lines(p.read_text(encoding="utf-8", errors="replace")):
            findings.append(
                Finding("md-atx", f"{raw}:{line_no}: `#123` in column 1 becomes MD018")
            )
    return findings


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


def _check_stale_anchors() -> list[Finding]:
    """Citations of a numbered section that the cited document no longer has.

    ⚠ **Scope is wider than the repo, and that is the point.** Of the four live
    dead `§4a` citations found by hand when this class recurred a third time,
    **two were in the memory tree** — which no repo-scoped check can reach, and
    which is why this leg lives here rather than in the CI-gating
    ``sanity_checks``. The repo half alone would have reported clean.
    """
    repo_root = Path.cwd()
    sources = [p for root in ANCHOR_ROOTS for p in sorted(Path(root).rglob("*.md"))]
    sources += [Path(f) for f in anchor_files() if Path(f).is_file()]
    resolve = default_resolver(repo_root, MEMORY_DIR if MEMORY_DIR.is_dir() else None)

    findings = [
        Finding("stale-anchors", describe(cite, target, repo_root))
        for cite, target in scan(sources, resolve)
    ]
    if MEMORY_DIR.is_dir():
        findings += [
            Finding("stale-anchors", describe(cite, target, repo_root))
            for cite, target in scan(
                sorted(MEMORY_DIR.glob("*.md")), resolve, root=MEMORY_DIR.parent
            )
        ]
    return findings


def in_linked_worktree(rev_parse_output: str) -> bool:
    """Is this a linked worktree rather than the repo's main checkout?

    ``git rev-parse --git-dir --git-common-dir`` prints two lines. They are the
    same path in a main checkout and differ in a linked worktree, where the
    first points at ``<common>/worktrees/<name>``.

    Unparseable output reads as NOT a worktree on purpose. The only caller uses
    this to DOWNGRADE a finding to a skip, so an answer it cannot read must
    leave the finding standing rather than silently clearing it.
    """
    lines = [ln.strip() for ln in rev_parse_output.splitlines() if ln.strip()]
    if len(lines) != 2:
        return False
    git_dir, common = (Path(ln).resolve() for ln in lines)
    return git_dir != common


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


def _handoff_size_result(
    handoff: str, runner: Runner = _run, budgets: Budgets | None = None
) -> CheckResult:
    """Size the handoff, or say WHY it could not be sized.

    Absent-because-worktree and absent-because-nobody-wrote-one are different
    claims, and rendering both as the same red is what teaches a reader to skip
    the leg — the failure mode ``CheckResult.note`` was added to avoid. A linked
    worktree is a tracked-files-only checkout and the handoff is gitignored, so
    there its absence is structural and says nothing about the branch.

    The skip is deliberately narrow: absence in a normal checkout stays a hard
    finding, because there it means exactly what this leg exists to catch. That
    is the ``sensitive-terms`` trade made the other way, and the asymmetry is
    the reason — that gate guards an irreversible publish, so "did not run" must
    never read as "passed"; this one guards a line budget, where firing on every
    worktree run costs more than it catches.
    """
    if not handoff and in_linked_worktree(
        runner(["git", "rev-parse", "--git-dir", "--git-common-dir"])
    ):
        return CheckResult(
            "handoff-size",
            skipped=f"no {HANDOFF} — linked worktree, gitignored files absent "
            "by construction",
        )
    return CheckResult("handoff-size", _check_handoff_size(handoff, budgets))


def _read_text_arg(path: str) -> str:
    """A composed text to screen. Never swallows a read error.

    ``_run``'s swallow-and-continue is right for one leg of a twelve-leg sweep
    and wrong here: an unreadable body would render as a clean single-check run,
    which is the report this mode exists to make impossible.
    """
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


# What this sweep does NOT do, named so that passing it cannot FEEL like
# passing the walk. Both parallel sessions on the 2026-08-25 wave hand-walked
# the mechanical half and skipped `make preflight` and `/post-branch` itself —
# neither was being careless, and both had the rule in context. The hand-walk
# is the REACHABLE thing and the skill is not, so the fix belongs here rather
# than on another rule.
#
# ⚠ These are STEP numbers on purpose. The skill's phases 1-6 are table rows
# that declare no headings, so `tools/stale_anchors.py` correctly flags a
# "post-branch phase 4" citation from any other file as a dead anchor.
UNCOVERED_STEPS: tuple[tuple[str, str], ...] = (
    ("Step 1", "behaviour gate — is this PR user-facing?"),
    ("Steps 2-4", "walk each doc surface against the diff"),
    ("Steps 5, 5b", "MEMORY.md + SoT reconcile — run even if Step 1 says no"),
    (
        "Step 7",
        "`make preflight` (clean-clone gate; it REPLACES this branch's "
        "`make test`) and the visibility-flip decision, both BEFORE "
        "`gh pr create`",
    ),
    ("Step 6", "PR body"),
    ("Steps 10a-10c", "pre-merge check, handoff, re-verify PR state last"),
)


def uncovered_notice() -> list[str]:
    """The closing line: what a green sweep says nothing about.

    A green sweep is not a green branch. It carries none of the judgement in
    the steps below, and reading it as the walk is the exact substitution this
    notice exists to interrupt.
    """
    out = [
        "",
        "  NOT COVERED by this sweep — /post-branch owns these:",
    ]
    width = max(len(label) for label, _ in UNCOVERED_STEPS)
    # Wrapped rather than printed flat: one over-wide line drags the whole
    # block sideways in a terminal, which is the readability defect the
    # Telegram renderer folds to 46 columns to avoid.
    indent = " " * (6 + width + 2)
    for label, what in UNCOVERED_STEPS:
        wrapped = textwrap.wrap(what, width=72 - width)
        out.append(f"      {label:<{width}}  {wrapped[0]}")
        out.extend(f"{indent}{line}" for line in wrapped[1:])
    out += [
        "",
        "  This sweep is /post-branch's FIRST step, not a substitute for it.",
        "  Invoke the skill — a hand walk is not the walk.",
    ]
    return out


def render(
    results: Sequence[CheckResult], *, show_uncovered: bool = True
) -> tuple[list[str], int]:
    out = ["post_branch_checks — mechanical sweep", ""]
    total = 0
    for r in results:
        if r.skipped:
            out.append(f"  {r.name:<16} SKIPPED  ({r.skipped})")
            continue
        if not r.findings:
            out.append(f"  {r.name:<16} clean")
            if r.note:
                out.append(f"      note: {r.note}")
            continue
        total += len(r.findings)
        out.append(f"  {r.name:<16} {len(r.findings)} to triage")
        for f in r.findings:
            out.append(f"      {f.detail}")
        if r.note:
            out.append(f"      note: {r.note}")
    out += [
        "",
        f"  {total} finding(s). Each is a candidate to DISMISS in seconds, never an",
        "  automatic edit — a false positive costs a glance, a silent miss ships a",
        "  doc that reads as complete.",
    ]
    if show_uncovered:
        out += uncovered_notice()
    return out, total


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="post_branch_checks",
        description="Every mechanical /post-branch check, in one run.",
    )
    parser.add_argument(
        "--check",
        action="append",
        metavar="NAME",
        help="run only this named check; repeatable. Without `action=append` a "
        "second --check silently replaced the first and the sweep printed a "
        "complete-looking clean run of ONE leg.",
    )
    parser.add_argument(
        "--text",
        action="append",
        metavar="PATH",
        help="screen a composed PR title/body for sensitive terms before it is "
        "posted; `-` reads stdin, repeatable. Runs ONLY that check, needs no "
        "git surface, and GATES (exit 1 on a hit)",
    )
    parser.add_argument(
        "--exit-zero",
        action="store_true",
        help="always exit 0 (findings are advisory, not a gate)",
    )
    args = parser.parse_args(argv)

    if args.text:
        if args.check:
            # Honouring one and dropping the other is how a session reads a
            # pass it never asked for.
            print(
                "post_branch_checks: --text runs alone; drop --check",
                file=sys.stderr,
            )
            return 2
        try:
            # `-` labels as "stdin": the finding line is prose an operator
            # triages seconds before a flip, and a bare dash reads as a hyphen.
            texts = [("stdin" if p == "-" else p, _read_text_arg(p)) for p in args.text]
        except OSError as exc:
            print(f"post_branch_checks: {exc}", file=sys.stderr)
            return 2
        # `--text` is a focused pre-flip screen of one composed string, not the
        # branch walk — it runs alone and needs no git surface, so the step
        # list would be noise at the one moment the operator is triaging
        # seconds before a visibility flip.
        lines, total = render([sensitive_text_result(texts)], show_uncovered=False)
        for line in lines:
            print(line)
        return 0 if (args.exit_zero or total == 0) else 1

    results = gather()
    if args.check:
        # dict.fromkeys keeps the operator's order and drops a repeat.
        wanted = list(dict.fromkeys(args.check))
        unknown = [c for c in wanted if c not in {r.name for r in results}]
        if unknown:
            # ANY unknown name aborts the whole run rather than filtering to
            # the survivors: a typo among several would otherwise run the rest
            # and report clean, which is the failure this flag's own
            # repeatability bug already caused once.
            print(
                "post_branch_checks: no such check: " + ", ".join(unknown),
                file=sys.stderr,
            )
            return 2
        results = [r for r in results if r.name in wanted]
    lines, total = render(results)
    for line in lines:
        print(line)
    return 0 if (args.exit_zero or total == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
