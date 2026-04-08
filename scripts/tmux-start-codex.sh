#!/usr/bin/env bash
set -euo pipefail

SESSION=${1:-fqdn-updater-dev}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Session already exists: $SESSION"
  echo "Attach: tmux attach -t $SESSION"
  exit 0
fi

TMUX_CMD=$(cat <<INNER
export PATH="$HOME/.local/bin:$HOME/.pub-cache/bin:\$PATH" && \
cd "$ROOT" && \
exec codex --dangerously-bypass-approvals-and-sandbox
INNER
)

tmux new-session -d -s "$SESSION" -c "$ROOT" "bash -lc '$TMUX_CMD'"

echo "Started session: $SESSION"
echo "Project: $ROOT"
echo "Attach: tmux attach -t $SESSION"
