#!/usr/bin/env bash
# PreToolUse hook for Read and Grep: secrets and the live account's configuration are never read.
#
# Exit code 2 blocks the read and tells Claude why. Hooks are guardrails, not a security boundary:
# live keys never live on a machine that runs Claude Code.
set -u
command -v jq >/dev/null || { echo "Blocked: the .claude/hooks guardrails need jq (brew install jq / apt install jq)." >&2; exit 2; }
path=$(jq -r '.tool_input.file_path // .tool_input.path // empty')
[ -z "$path" ] && exit 0
root=${CLAUDE_PROJECT_DIR:-$(pwd)}
rel=${path#"$root"/}
rel=${rel#./}

block() {
  echo "Blocked by .claude/hooks/block_secrets.sh: $1" >&2
  exit 2
}

case "$rel" in
  .env.example | */.env.example) ;;  # variable names only
  .env | .env.* | *.env | */.env | */.env.*) block "$rel holds secrets: never read, print or copy it." ;;
  /etc/trades | /etc/trades/*) block "/etc/trades holds the trading machine's keys." ;;
  config/live.yaml) block "config/live.yaml is human-only (the live account and the gate thresholds)." ;;
  *credential* | *.pem | *id_rsa*) block "$rel looks like a credential." ;;
esac
exit 0
