# Coding Standards

Read at **review** time, not during implementation. The standards reviewer in the
`mattpocock-skills:code-review` skill (and any other reviewer) checks a diff against this file.

Mechanical rules are enforced by tooling and are NOT review findings: `make lint-py` (ruff
format + lint), `make typecheck` (mypy strict) and `make lint-md`. Skip anything those catch.

## Where the judgement-call standards live

This file currently points rather than restates, so the rules have one home:

- `AGENTS.md` → **Code Style** and **Testing**: annotations, mock-client injection, in-memory
  DuckDB, no network in tests.
- `AGENTS.md` → **Code-level rules**: restated constants, positional inserts, run-id
  namespacing, causal detectors, cost/stop-floor defaults, `realised_rr`, `passes_gate`.
- `AGENTS.md` → **Git Conventions** and **PR titles**.
- `.claude/hooks/context-map.json`: the edit-time cards. Each card's prose is a standard for the
  files its globs cover.

## Review checklist this repo adds to the smell baseline

- **A restated constant is the defect.** A gate threshold, cost or path written as a literal
  where an owning function or constant exists (`passes_gate`, `DEFAULT_DB_PATH`,
  `round_trip_drag_r`) is a finding even when the values agree today.
- **A guard needs a mutation case.** A new check, gate or hook without a test proving it can
  fail (teeth) and does not fire on clean input (specificity) is a finding.
- **A SKIP must not read as a PASS.** A check that degrades silently on missing input is a finding.
- **Causality.** A detector or study that reads bars after its decision bar is a finding;
  `tests/test_lookahead.py` covers registered detectors only.
