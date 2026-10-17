#!/usr/bin/env bash
# PreToolUse hook for Bash: commands that could reach real money, leak secrets, rewrite the record,
# or take one of Jack's decisions.
#
# Exit code 2 blocks the command and tells Claude why. Hooks are guardrails, not a security
# boundary: live keys never live on a machine that runs Claude Code.
set -u
command -v jq >/dev/null || { echo "Blocked: the .claude/hooks guardrails need jq (brew install jq / apt install jq)." >&2; exit 2; }
cmd=$(jq -r '.tool_input.command // empty')
[ -z "$cmd" ] && exit 0

block() {
  echo "Blocked by .claude/hooks/block_live.sh: $1" >&2
  exit 2
}
has() { printf '%s' "$cmd" | grep -Eiq -- "$1"; }

# Real money: Alpaca's live and broker endpoints, or switching the paper flag off.
has '(^|[^a-z0-9.-])api\.alpaca\.markets' && block "the live Alpaca endpoint is off limits (paper-api.alpaca.markets only)."
has 'broker-api\.alpaca\.markets' && block "Alpaca's broker API is off limits."
has 'ALPACA_PAPER_TRADE[[:space:]]*=[[:space:]]*["'"'"']?(false|0|no)' && block "paper trading stays on."
has 'config/live\.yaml' && block "config/live.yaml is human-only."

# Secrets: env files, the trading machine's key file, and printing the environment.
stripped=$(printf '%s' "$cmd" | sed -E 's/\.env\.example//g')
printf '%s' "$stripped" | grep -Eq '(^|[^a-zA-Z0-9_])\.env([^a-zA-Z0-9_]|$)|[a-zA-Z0-9_]\.env([^a-zA-Z0-9_]|$)' \
  && block "env files hold secrets: never read, print or copy them."
has '/etc/trades' && block "/etc/trades holds the trading machine's keys."
has '(^|[;&|(`])[[:space:]]*(printenv|env|set|export[[:space:]]+-p|declare[[:space:]]+-x)[[:space:]]*($|[;&|)`])' \
  && block "printing the environment would show secrets."
has '(^|[;&|(`])[[:space:]]*printenv[[:space:]]' && block "printing the environment would show secrets."
has '\$\{?(APCA_|TELEGRAM_BOT|MASSIVE_API_KEY|TRADES_PASSWORD|TRADES_SECRET)' && block "that variable is a secret."

# The record and the rules: human-only files, research runs, paper records, the guardrails.
# (A command that writes: an in-place edit, tee, cp, mv, rm, truncate, a git restore, or a redirect.)
write='(^|[;&|(`[:space:]])(sed[[:space:]]+(-[a-z]*[[:space:]]+)*-[a-z]*i|perl[[:space:]]+-[a-z]*i|tee|cp|mv|rm|truncate|chmod|git[[:space:]]+(checkout|restore|rm))[[:space:]][^;&|]*'
into='>>?[[:space:]]*["'"'"']?(\./)?'
has "${write}(risk/limits\.yaml|config/backtest\.yaml)|${into}(risk/limits\.yaml|config/backtest\.yaml)" \
  && block "risk/limits.yaml and config/backtest.yaml are human-only: propose the change in chat."
has "${write}(^|[[:space:]/\"'])(runs|paper)(/|[[:space:]]|$)|${into}(runs|paper)/" \
  && block "runs/ and paper/ are records: deleting or rewriting them would hide tested variants or fake evidence."
has 'git[[:space:]]+clean' && block "git clean could delete research runs and paper records."
has "${write}[^;&|]*\.claude/(hooks|settings)|${into}\.claude/(hooks|settings)" && block "the guardrails themselves are human-only."

# Jack's decisions.
has 'trades[[:space:]]+spec[[:space:]]+freeze' && block "freezing a spec is Jack's decision."
has 'open-locked-test' && block "opening a locked test is Jack's decision (it can be done once)."
has 'trades[[:space:]]+gate[^;&|]*--attest' && block "gate attestations are Jack's to make."
has 'trades[[:space:]]+(paper[[:space:]]+)?resume' && block "turning the kill switch off is Jack's decision."
has '(rm|unlink|mv|git[[:space:]]+rm)[^;&|]*(^|[[:space:]/])HALT([[:space:]]|$)' && block "turning the kill switch off is Jack's decision."
has 'trades[[:space:]]+paper[^;&|]*--submit' && block "paper orders come from the scheduled runner, not from a Claude session."
has 'trades[[:space:]]+paper[[:space:]]+drill' && block "kill switch drills are evidence for the gate: Jack runs them."

# Shared history.
has 'git[[:space:]]+push[^;&|]*([[:space:]:+]|^)(main|master)([[:space:]]|$)' && block "never push to main: push a branch and open a pull request."
has 'git[[:space:]]+push[^;&|]*(--force([[:space:]]|$|=)|[[:space:]]-f([[:space:]]|$))' && block "no force pushes."
exit 0
