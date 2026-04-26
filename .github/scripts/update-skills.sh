#!/usr/bin/env bash
# update-skills.sh — validate every project SKILL.md has frontmatter delimiters.
# Stub for the update-skills.yaml workflow. Future: pull from a shared skill registry.
set -euo pipefail

SKILLS_DIR=".claude/skills"
fail=0

for skill_md in "$SKILLS_DIR"/*/SKILL.md; do
  if ! head -1 "$skill_md" | grep -q '^---$'; then
    echo "ERR: $skill_md missing opening frontmatter delimiter"
    fail=1
    continue
  fi
  # Confirm the frontmatter has a closing delimiter on or before line 30.
  if ! head -30 "$skill_md" | tail -29 | grep -q '^---$'; then
    echo "ERR: $skill_md missing closing frontmatter delimiter within first 30 lines"
    fail=1
  fi
done

exit "$fail"
