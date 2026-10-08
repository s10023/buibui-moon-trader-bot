# Step 7 — the clean-clone pre-flight

**When:** Read before running `make preflight` at Step 7, and before deciding to skip it.

Reference for `.claude/skills/post-branch/SKILL.md`, which holds the run order and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this file", "this skill", "above" or "below" about the phase table or a step, it means SKILL.md.

```bash
make preflight
```

**Order is load-bearing: this runs AFTER the commit above, never before.** A
clone only ever sees *committed* state, so running it earlier — in phase 0's
sweep, say — tests stale HEAD and reports green while the doc commits this
phase just produced go untested. The script refuses outright on a dirty tree
rather than reporting that green.

**It IS this branch's one full-suite run** — measured 2026-08-20 on 4205 tests:
**300.1s against `make test`'s 294.9s, +1.8%**, so the hermetic form costs five
seconds. ⚠ **Running `make test` first and then this is the same suite twice for
nothing**, and it happened on 2026-08-26 to a session reading the old "supersedes
the final `make test`" wording, which reads as an exception to a gate rather than
as a replacement for it.

**On a host where it exits 3 (`INFRA`)** — the Windows laptop, where the clone cannot
`poetry install` numpy — run `make test` as the substitute, name it in the PR body, and
state that CI is the only clean-clone verifier for this branch.

⚠ **Do not read "replacement" as "stop running `make test` while you work."** A
clone cannot see uncommitted code — the same property that makes this correct at
Step 7 makes it useless mid-branch, and it refuses on a dirty tree rather than
pretending otherwise. `make test` stays the tool until the work is committed. It clones to a temp dir with
`--no-hardlinks` (plain `--local` fails `Invalid cross-device link` onto `/tmp`
here), runs `poetry install --no-root` in the clone (**6.69s** against a warm
cache), then the same pytest invocation `make test` uses.

**Why it exists here rather than as another CI job: CI already IS this gate** —
a clean checkout, which is why it caught #666. The gap is TIMING. On a private
repo, detection after a push costs a metered cycle, a red PR and a visibility
flip just to read the failure. This is the last moment that is still free.

⚠ **Read the banner, not the exit code** — make collapses any recipe failure to
its own exit 2. `REFUSED` means the tree was dirty and nothing ran; `INFRA`
means the clone or install died; only `FAILED` is a real finding. ⚠ **On the Windows
host, compare a `FAILED` set against the host baseline tracked in #869 before
diagnosing** — an exact match is that baseline, any other failure is a real finding.
Drop this sentence when #869 closes.

⚠ **Two things it does NOT cover**, so do not read a pass as a clean bill: an
*absolute* default (`$HOME/...`) survives a clone untouched — `EXTERNAL_LEDGERS`
in `deploy/backup-analytics.sh` is that shape — and it only sees code some test
actually exercises, never an untested CLI branch.

⚠ **Scope: run it when the diff can REACH the suite, and prove that with a check rather than a
judgement.** The positive test is two greps — does the diff contain Python, and does any test
read a changed path (`grep -rl <changed-path> tests/`)? If both answer no, the clone re-runs
the whole suite to reproduce `main`'s own result; **say in the PR body which gate you ran
instead and why**, naming the two greps. Measured on #730, a lockfile line plus a
`.github/dependabot.yml` entry: zero Python, no test reading either file, 4762 tests that
could not be affected.

⚠ **A `grep -rl` hit inside a COMMENT or DOCSTRING still counts as a RUN.** The check cannot
tell a citation from a read, and it is only allowed to be wrong in the expensive direction.
Measured on #736: a doc-only amend to `.claude/context/signals.md` hit
`tests/test_post_branch_checks.py`, where the path appears in a docstring naming that file as
a known false positive while the test itself runs on a synthetic fixture — so a 5-minute
clean-clone run was spent on a diff that provably could not reach the suite. ⛔ **Do not fix
that by narrowing the grep**: the spec-reconcile counter cannot tell a citation from a
disclaimer either, and every narrowing there re-created the blindness it was meant to remove.

⛔ **The default stays RUN, and the discriminator must be that positive check — never "this
looks harmless".** That judgement is exactly what #586 and #666 defeated, both of which were
diffs nobody expected to reach anything. This scope line exists because a gate with no stated
scope makes the correct call look like a deviation, which is how it gets dropped later on a
diff that DID need it.
