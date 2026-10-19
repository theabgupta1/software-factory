#!/usr/bin/env bash
# Install (or upgrade) the Software Factory into your Claude Code home. See scripts/install.py for options.
set -euo pipefail
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install.py" install "$@"
