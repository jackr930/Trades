#!/usr/bin/env bash
# Open an issue labelled journal-health, or comment on the one already open, when a journal
# workflow job fails. GitHub emails the repository's watchers about both.
# Usage: alert.sh <job name>   (needs GH_TOKEN, GH_REPO and RUN_URL in the environment)
set -euo pipefail

{
  echo "The **$1** job failed on $(date -u +%Y-%m-%d): $RUN_URL"
  if [ -s health.txt ]; then
    echo
    echo "\`trades journal health\` says:"
    echo
    cat health.txt
  fi
  echo
  echo "Close this issue once it is fixed; the next failure opens a new one."
} > alert.md

gh label create journal-health --color B60205 --description "The journal workflow needs attention" --force
n=$(gh issue list --label journal-health --state open --json number --jq '.[0].number')
if [ -n "$n" ]; then
  gh issue comment "$n" --body-file alert.md
else
  gh issue create --title "Journal needs attention" --label journal-health --body-file alert.md
fi
