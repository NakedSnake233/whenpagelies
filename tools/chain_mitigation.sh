#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
export TMPDIR="$PWD/.tmp"
export XDG_CACHE_HOME="$PWD/.cache"
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/ms-playwright"
export PYTHONUNBUFFERED=1
exec .venv/bin/python -m tools.chain_mitigation
