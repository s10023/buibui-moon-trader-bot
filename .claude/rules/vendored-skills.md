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

## ⚠ On Windows the symlink checks out as a TEXT FILE, and nothing says so

Creating a symlink on Windows needs Developer Mode or an elevated shell, so a clone made
without either sets `core.symlinks=false` and writes `.claude/skills/humanizer` as a
**30-byte regular file whose contents are the link target** — the literal string
`../../.agents/skills/humanizer`. Measured 2026-09-19 on the Windows laptop: 27 of 28 skills
loaded and `ls .claude/skills/humanizer/SKILL.md` returned `Not a directory`.

- **The skill is silently absent, not broken-with-an-error.** The harness injects the skills
  it can find; one that resolves to a file is simply not among them, so the failure looks
  like the skill never existed rather than like a bad checkout.
- ✅ **It CANNOT leak back into a commit.** Git's index still holds mode `120000` and compares
  against the link text, which matches byte-for-byte, so `git status` stays clean. The damage
  is confined to the working tree.
- ⛔ **`git config core.symlinks true` alone does NOT fix it** — the re-checkout then fails
  `unable to create symlink: Permission denied` and **leaves the path deleted**. Restore with
  `git config core.symlinks false && git checkout -- .claude/skills/humanizer` before doing
  anything else. The one-time fix is Developer Mode (Settings → System → For developers), then
  `core.symlinks=true` and a re-checkout.
- **Invisible to CI by construction**, which runs on Linux where the symlink resolves — so no
  gate anywhere will ever report this.

## What is still owed

- **No refresh CI.** Nothing re-fetches upstream, re-computes the hash, or opens a bump PR,
  so a stale vendored skill goes unnoticed indefinitely. The reference standard calls for a
  weekly refresh PR.
- **No local verifier**, because the hash algorithm is not recorded. Recording it is the
  prerequisite for the refresh job, not a separate nicety.
- **No leg asserting every `.claude/skills/*` entry resolves to a `SKILL.md`.** Deliberately
  NOT added with the section above: `make sanity-checks` gates in CI, so such a leg is a no-op
  on Linux and would sit PERMANENTLY RED locally until Developer Mode is enabled — the
  "a permanently-red line teaches its reader to skip it" failure `AGENTS.md` already names.
  Add it in the same change that enables Developer Mode, never before.

Source: `docs/research/2026-08-19-cross-repo-workflow-standard.md` §3.
