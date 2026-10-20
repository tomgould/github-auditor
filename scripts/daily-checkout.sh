#!/usr/bin/env bash
# Daily checkout: fetch updates and clone new repos for every configured account.
# Run it from cron (see "Automated Daily Sync" in the README).
#
# Optional environment variables:
#   AUDITER_LOG   log file (default: ~/.cache/github-auditor/checkout.log)
#   AUDITER_BIN   the auditer command (default: auditer, found on PATH)
set -euo pipefail

# Run from the repository root, so config.yaml and the data folder are found.
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# cron has a minimal PATH; add the usual per-user install location.
export PATH="$HOME/.local/bin:$PATH"

LOG="${AUDITER_LOG:-$HOME/.cache/github-auditor/checkout.log}"
mkdir -p "$(dirname "$LOG")"

# Send all output to the log file (append).
exec >> "$LOG" 2>&1

echo "=== GitHub Auditor checkout: $(date) ==="
"${AUDITER_BIN:-auditer}" checkout --all --full
echo "=== Done: $(date) ==="
