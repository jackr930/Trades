#!/usr/bin/env bash
# PostToolUse hook for Edit, Write and NotebookEdit: after a Python change, the unit tests (about 20s).
# It cannot undo the edit; exit code 2 sends the failures back to Claude to fix.
set -u
command -v jq >/dev/null || { echo "Blocked: the .claude/hooks guardrails need jq (brew install jq / apt install jq)." >&2; exit 2; }
path=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // empty')
case "$path" in
  *.py) ;;
  *) exit 0 ;;
esac
root=${CLAUDE_PROJECT_DIR:-$(pwd)}
case "${path#"$root"/}" in
  src/* | tests/*) ;;
  *) exit 0 ;;
esac
cd "$root" || exit 0
if [ -x .venv/bin/python ]; then py=.venv/bin/python; elif command -v uv >/dev/null; then py="uv run python"; else py=python3; fi
if ! out=$($py -m pytest -q -x tests/unit -p no:cacheprovider 2>&1); then
  printf '%s\n' "$out" | tail -40 >&2
  echo "The unit tests fail after this edit: fix them before going on." >&2
  exit 2
fi
exit 0
