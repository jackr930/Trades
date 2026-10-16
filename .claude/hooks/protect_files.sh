#!/usr/bin/env bash
# PreToolUse hook for Edit, Write and NotebookEdit: the files only Jack may change.
#
# Exit code 2 blocks the edit and tells Claude why. Hooks are guardrails, not a security boundary:
# live keys never live on a machine that runs Claude Code.
set -u
command -v jq >/dev/null || { echo "Blocked: the .claude/hooks guardrails need jq (brew install jq / apt install jq)." >&2; exit 2; }
input=$(cat)
path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_input.notebook_path // empty')
[ -z "$path" ] && exit 0
root=${CLAUDE_PROJECT_DIR:-$(pwd)}
rel=${path#"$root"/}
rel=${rel#./}
text=$(printf '%s' "$input" | jq -r '[.tool_input.content, .tool_input.new_string, .tool_input.new_source] | map(select(. != null)) | join("\n")')

block() {
  echo "Blocked by .claude/hooks/protect_files.sh: $1 Propose the change in chat instead; Jack makes it." >&2
  exit 2
}

case "$rel" in
  .env.example) ;;  # variable names only: safe to edit
  .env | .env.* | *.env | */.env | */.env.*) block "$rel holds secrets." ;;
  config/live.yaml) block "config/live.yaml is human-only (the live account and the gate thresholds)." ;;
  risk/limits.yaml) block "risk/limits.yaml is human-only (the risk limits every order passes through)." ;;
  config/backtest.yaml) block "config/backtest.yaml is human-only (the acceptance thresholds a strategy must meet)." ;;
  runs/*) block "runs/ is the research record: only \`trades research\` writes it." ;;
  paper/*) block "paper/ is the paper-trading record (orders, fills, drills, attestations): only the paper trader writes it." ;;
  .claude/settings.json | .claude/settings.local.json | .claude/hooks/*) block "the guardrails themselves are human-only." ;;
  *secret* | *credential* | *.pem | *.key | *id_rsa*) block "$rel looks like a credential." ;;
esac

# Specs: Claude may draft them, but freezing is Jack's, and a frozen spec's rules never change.
case "$rel" in
  specs/*.md)
    if [ -f "$path" ] && grep -Eq '^frozen:[[:space:]]*[0-9]{4}-' "$path"; then
      block "$rel is frozen: its rules and locked test are fixed. \`trades spec version\` starts a new draft."
    fi
    if printf '%s' "$text" | grep -Eq '^(frozen:[[:space:]]*[0-9]{4}-|rules_hash:[[:space:]]*[0-9a-f]{6,})'; then
      block "only \`trades spec freeze\` (run by Jack) freezes a spec."
    fi
    ;;
esac
exit 0
