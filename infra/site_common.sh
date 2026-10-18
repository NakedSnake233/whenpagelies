#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ $# != 1 ]]; then
    echo "Usage: $0 SITE" >&2
    exit 2
fi
site=$1
python=.venv/bin/python
[[ -x $python ]] || python=python3
config=$("$python" infra/site_config.py "$site")
mapfile -t settings <<< "$config"
image=${settings[0]}
main_port=${settings[1]}
envctrl_port=${settings[2]}
ports=("${settings[@]:3}")
name=osd-$site
