#!/usr/bin/env bash
set -euo pipefail

SESSION=${1:-fqdn-updater}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux new-session -d -s "$SESSION" -c "$ROOT" "bash -lc 'cd "$ROOT" && exec bash'"
fi

tmux send-keys -t "$SESSION" "export PATH=\"$HOME/.local/bin:$HOME/.pub-cache/bin:\$PATH\"" Enter

if [ -n "$ROOT" ]; then
  tmux send-keys -t "$SESSION" "cd \"$ROOT\"" Enter
fi

echo "Attach: tmux attach -t $SESSION"
