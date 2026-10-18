#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/site_common.sh"
mkdir -p logs
exec >> "logs/health-${site}.log" 2>&1
printf '[%s] Waiting for %s (main=%s envctrl=%s)\n' "$(date -Is)" "$name" "$main_port" "$envctrl_port"
deadline=$((SECONDS + 900))
while (( SECONDS < deadline )); do
    remaining=$((deadline - SECONDS))
    budget=$((remaining < 5 ? remaining : 5))
    health=000
    health=$(curl --noproxy '*' -s --connect-timeout 2 --max-time "$budget" -o /dev/null -w '%{http_code}' "http://127.0.0.1:${envctrl_port}/health") || health=000
    main=000
    remaining=$((deadline - SECONDS))
    if (( remaining > 0 )); then
        budget=$((remaining < 5 ? remaining : 5))
        main=$(curl --noproxy '*' -s --connect-timeout 2 --max-time "$budget" -o /dev/null -w '%{http_code}' "http://127.0.0.1:${main_port}/") || main=000
    fi
    printf '[%s] health=%s main=%s\n' "$(date -Is)" "$health" "$main"
    # Some env-ctrl servers do not expose /health; main-site readiness is authoritative.
    if [[ $main == 2?? || $main == 3?? ]]; then
        echo HEALTHY
        exit 0
    fi
    remaining=$((deadline - SECONDS))
    (( remaining > 0 )) || break
    sleep "$((remaining < 10 ? remaining : 10))"
done
echo TIMEOUT
exit 1
