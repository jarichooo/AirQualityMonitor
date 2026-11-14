#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

for py_cmd in python3 python; do
  if command -v "$py_cmd" >/dev/null 2>&1 && "$py_cmd" -c "import sys; sys.exit(0 if sys.version_info.major >= 3 else 1)" >/dev/null 2>&1; then
    exec "$py_cmd" .sim_client.py stop "$@"
  fi
done

if command -v py >/dev/null 2>&1 && py -3 -c "import sys" >/dev/null 2>&1; then
  exec py -3 .sim_client.py stop "$@"
fi

echo "Python 3 is required to run this script." >&2
exit 1
