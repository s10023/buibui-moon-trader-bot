#!/usr/bin/env bash
# update-skills.sh — validate every project SKILL.md has frontmatter delimiters.
# Stub for the update-skills.yaml workflow. Future: pull from a shared skill registry.
# CR is stripped before matching: a Windows checkout under core.autocrlf=true writes
# every SKILL.md as CRLF, and `^---$` never matches `---\r` (#900).
set -euo pipefail

SKILLS_DIR="${SKILLS_DIR:-.claude/skills}"
fail=0

for skill_md in "$SKILLS_DIR"/*/SKILL.md; do
  if ! head -1 "$skill_md" | tr -d '\r' | grep -qx -- '---'; then
    echo "ERR: $skill_md missing opening frontmatter delimiter"
    fail=1
    continue
  fi
  # Confirm the frontmatter has a closing delimiter on or before line 30.
  if ! sed -n '2,30p' "$skill_md" | tr -d '\r' | grep -qx -- '---'; then
    echo "ERR: $skill_md missing closing frontmatter delimiter within first 30 lines"
    fail=1
  fi
done

exit "$fail"
