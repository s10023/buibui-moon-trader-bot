# Vendored skills

Path-scoped rule. Delivered at edit time by the `vendored-skills` card in
`.claude/hooks/context-map.json`; this file is its tracked prose home.

## The layout

| Path | What it is |
| --- | --- |
| `.agents/skills/<name>/` | the real directory — the vendored copy |
| `.claude/skills/<name>` | a **relative** symlink into `.agents/`, so it resolves on any clone |
| `skills-lock.json` | the hash pin: `source`, `sourceType`, `skillPath`, `computedHash` |

Currently vendored: `humanizer` from `blader/humanizer`.

## All three are committed on purpose

They were gitignored until 2026-08-19 — `.gitignore` ignored `skills-lock.json` and
`.agents/`, and `.git/info/exclude` hid the symlink. That is the exact inverse of what a
pin is for: **a hash nobody can verify is not a pin**, and `.git/info/exclude` does not
survive a clone either, so a reclone got a dead symlink pointing at an absent directory.
Do not re-ignore them.

## Your local edit is silently overwritten

A vendored copy is replaced wholesale on the next refresh. Fix the problem **upstream** and
re-vendor; a hand-patch to `.agents/skills/<name>/` looks like it worked and disappears
without warning.

## `computedHash` is not a plain sha256

`skills-lock.json` records `44201a7a…` for `humanizer`, while `sha256sum` of its `SKILL.md`
is `457d06df…`. That is **not** evidence of a corrupt pin — the vendoring tool normalises
before hashing, and the algorithm is not recorded here. So the pin cannot be checked from
this repo alone.

**Do not "correct" the hash by hand**, and do not read a mismatch against `sha256sum` as
tampering.

## What is still owed

- **No refresh CI.** Nothing re-fetches upstream, re-computes the hash, or opens a bump PR,
  so a stale vendored skill goes unnoticed indefinitely. The reference standard calls for a
  weekly refresh PR.
- **No local verifier**, because the hash algorithm is not recorded. Recording it is the
  prerequisite for the refresh job, not a separate nicety.

Source: `docs/research/2026-08-19-cross-repo-workflow-standard.md` §3.
