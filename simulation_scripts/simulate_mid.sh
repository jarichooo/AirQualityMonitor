#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

for py_cmd in python3 python; do
  if command -v "$py_cmd" >/dev/null 2>&1 && "$py_cmd" -c "import sys; sys.exit(0 if sys.version_info.major >= 3 else 1)" >/dev/null 2>&1; then
    exec "$py_cmd" -m ai_model.lan_runner mid "$@"
  fi
done

if command -v py >/dev/null 2>&1 && py -3 -c "import sys" >/dev/null 2>&1; then
  exec py -3 -m ai_model.lan_runner mid "$@"
fi

echo "Python 3 is required to run this script." >&2
exit 1
