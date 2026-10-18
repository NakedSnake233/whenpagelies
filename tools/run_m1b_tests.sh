#!/usr/bin/env bash
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export TMPDIR="$PWD/.tmp" XDG_CACHE_HOME="$PWD/.cache" PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/ms-playwright" PYTHONUNBUFFERED=1
.venv/bin/python -m pytest -q -m sites -o markers=sites --junitxml=logs/m1b_sites.xml -o cache_dir=.cache/pytest-m1b --basetemp=.tmp/pytest-m1b "$@"
result=$?
printf '%s\n' "$result" > "logs/${OSD_SITE_RUN_NAME:-m1b_sites}.exit"
exit "$result"
