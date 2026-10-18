#!/bin/bash
set -u
export TMPDIR="$PWD/.tmp" XDG_CACHE_HOME="$PWD/.cache" PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/ms-playwright"
.venv/bin/python -m pytest -q -o markers=sites "$@"
result=$?
printf '%s\n' "$result" > "logs/${OSD_CHECK_NAME:-m1c-check}.exit"
exit "$result"
