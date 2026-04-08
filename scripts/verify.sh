#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ -x ".venv/bin/ruff" ]; then
  RUFF=".venv/bin/ruff"
else
  RUFF="ruff"
fi

if [ -x ".venv/bin/pytest" ]; then
  PYTEST=".venv/bin/pytest"
else
  PYTEST="pytest"
fi

"$RUFF" format . --check
"$RUFF" check .
"$PYTEST"
