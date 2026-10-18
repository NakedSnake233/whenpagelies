#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
failed=0
for site in shopping_admin reddit shopping gitlab; do
    image="am1n3e/webarena-verified-${site}"
    printf '[%s] Starting %s\n' "$(date -Is)" "$image"
    if docker image inspect "$image" >/dev/null 2>&1; then
        printf '[%s] Reusing existing image %s without modification\n' "$(date -Is)" "$image"
    elif ! docker pull "$image"; then
        printf '[%s] FAILED %s\n' "$(date -Is)" "$image"
        failed=1
        continue
    fi
    if docker tag "$image" "osd-${site}:latest"; then
        printf '[%s] Finished %s (alias osd-%s:latest)\n' "$(date -Is)" "$image" "$site"
    else
        failed=1
    fi
done
exit "$failed"
