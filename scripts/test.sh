#!/usr/bin/env bash
# One command for every test layer: scripts/test.sh [layer ...] [--matrix]. See scripts/testkit.py.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec python3 scripts/testkit.py "$@"
