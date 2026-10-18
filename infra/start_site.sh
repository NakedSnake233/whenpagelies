#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/site_common.sh"
# Fail on daemon errors instead of treating them as an absent container.
docker info >/dev/null
existing=$(docker ps -a --filter "name=^/${name}$" --format '{{.Names}}')
if [[ $existing == "$name" ]]; then
    if [[ $(docker inspect --format '{{.State.Running}}' "$name") == true ]]; then
        echo "$name is already running"
        exit 0
    fi
    "$python" infra/site_config.py "$site" check-ports
    docker start "$name"
else
    "$python" infra/site_config.py "$site" check-ports
    docker image inspect "$image" >/dev/null 2>&1 || {
        echo "Image $image is not available yet; wait for the background pull." >&2
        exit 1
    }
    alias_image=osd-${site}:latest
    docker tag "$image" "$alias_image"
    args=()
    for port in "${ports[@]}"; do args+=(-p "$port"); done
    docker run -d --name "$name" --restart unless-stopped "${args[@]}" "$alias_image"
fi
