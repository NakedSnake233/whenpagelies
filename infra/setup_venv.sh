#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export XDG_CACHE_HOME="$PWD/.cache"
export UV_CACHE_DIR="$PWD/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PWD/.cache/uv-python"
export UV_PYTHON_BIN_DIR="$PWD/.cache/bin"
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/ms-playwright"
export TMPDIR="$PWD/.tmp"
export PIP_CACHE_DIR="$PWD/.cache/pip"
export PYTHONUSERBASE="$PWD/.cache/python-user"
mkdir -p "$TMPDIR" "$XDG_CACHE_HOME"
export UV_PYTHON_PREFERENCE=only-managed
printf '[%s] Installing managed Python 3.11 inside the repo\n' "$(date -Is)"
uv python install 3.11
uv venv --python 3.11 --clear .venv
uv pip install --python .venv/bin/python mitmproxy playwright browsergym-core webarena-verified httpx pyyaml pymysql 'psycopg[binary]' pandas matplotlib pytest python-dotenv tenacity
printf '[%s] Installing Chromium\n' "$(date -Is)"
.venv/bin/playwright install chromium
printf '[%s] SKIPPED playwright install-deps chromium: system package installation would write outside the project; no sudo used.\n' "$(date -Is)"
printf '[%s] Python environment setup complete\n' "$(date -Is)"
