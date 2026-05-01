#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ -x ".venv/bin/ruff" ]; then
  RUFF=".venv/bin/ruff"
else
  RUFF="ruff"
fi

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python3"
fi

"$RUFF" format . --check
"$RUFF" check .
"$PYTHON" -m pytest
