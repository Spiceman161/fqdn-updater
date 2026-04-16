#!/usr/bin/env bash
set -euo pipefail

SESSION=${1:-fqdn-updater-dev}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS_FILE="$ROOT/AGENTS.md"

if [ ! -s "$AGENTS_FILE" ]; then
  echo "Missing project instructions: $AGENTS_FILE" >&2
  exit 1
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Session already exists: $SESSION"
  echo "Attach: tmux attach -t $SESSION"
  exit 0
fi

TMUX_CMD=$(cat <<INNER
export PATH="$HOME/.local/bin:$HOME/.pub-cache/bin:\$PATH" && \
cd "$ROOT" && \
codex \
  --cd "$ROOT" \
  -c project_doc_max_bytes=65536 \
  --dangerously-bypass-approvals-and-sandbox; \
rc=\$?; \
echo; \
echo "[tmux-start-codex] codex exited with code \$rc"; \
echo "[tmux-start-codex] press Enter or run commands manually"; \
exec bash
INNER
)

tmux new-session -d -s "$SESSION" -c "$ROOT" "bash -lc '$TMUX_CMD'"

echo "Started session: $SESSION"
echo "Project: $ROOT"
echo "Attach: tmux attach -t $SESSION"
