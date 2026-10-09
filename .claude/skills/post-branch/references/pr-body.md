# Step 8 — the PR body's Documentation updates section

**When:** Read at Step 8, after the PR exists.

Reference for `.claude/skills/post-branch/SKILL.md`, which holds the run order and the condensed rules. This file holds the full text, including the dated incidents behind each rule. The passages below are carried over verbatim from the pre-split SKILL.md (#884), so where one says "this file", "this skill", "above" or "below" about the phase table or a step, it means SKILL.md.

Once edits are approved and applied (or the gate decided no edits were
needed), append a "Documentation updates" section to the PR body so
reviewers see the doc reasoning:

```markdown
## Documentation updates

- `AGENTS.md`: rewrote Project Structure entry for `analytics/store/` after
  data_store.py reduced to a re-export shim
- `README.md`: no change needed (no CLI surface change)
- `MEMORY.md`: Current State updated with strat-2 summary
```

Use the three-step fetch → append → push sequence:

```bash
# 1. Fetch the current body
gh api repos/s10023/buibui-moon-trader-bot/pulls/<PR#> --jq .body > /tmp/pr_body.md

# 2. Append the new section (Edit tool, or heredoc)
cat >> /tmp/pr_body.md <<'EOF'

## Documentation updates

- `<file>`: <what changed>
EOF

# 3. Push the new body
gh pr edit <PR#> --body-file /tmp/pr_body.md
```

If the original PR body already has a "Documentation updates" section, open
`/tmp/pr_body.md` in the Edit tool and update it in place — don't append a
duplicate.
