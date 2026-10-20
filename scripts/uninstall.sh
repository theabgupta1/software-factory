#!/usr/bin/env bash
# Remove what scripts/install.sh installed and restore what it replaced. See scripts/install.py for options.
set -euo pipefail
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install.py" uninstall "$@"
