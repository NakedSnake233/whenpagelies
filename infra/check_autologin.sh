#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/site_common.sh"
mkdir -p .tmp
response=$(mktemp .tmp/autologin.XXXXXX)
trap 'rm -f "$response"' EXIT
case "$site" in
    shopping_admin)
        code=$(curl --noproxy '*' -s --connect-timeout 3 --max-time 20 \
            -H 'X-M2-Admin-Auto-Login: admin:admin1234' \
            -D "$response" -o /dev/null -w '%{http_code}' \
            "http://127.0.0.1:${main_port}/admin")
        if [[ $code == 3?? ]] && grep -Eiq '^location:.*dashboard' "$response"; then
            echo 'PASS: shopping_admin redirects to the dashboard'
        else
            echo "FAIL: shopping_admin expected a dashboard redirect (HTTP $code)" >&2
            exit 1
        fi
        ;;
    reddit)
        code=$(curl --noproxy '*' -s --connect-timeout 3 --max-time 20 \
            -H 'X-Postmill-Auto-Login: MarvelsGrantMan136:test1234' \
            -o "$response" -w '%{http_code}' "http://127.0.0.1:${main_port}/")
        if [[ $code == 2?? ]] && grep -Fq 'MarvelsGrantMan136' "$response"; then
            echo 'PASS: reddit page contains the auto-login username'
        else
            echo "FAIL: reddit expected the auto-login username (HTTP $code)" >&2
            exit 1
        fi
        ;;
    *) echo "Auto-login check is unsupported for $site" >&2; exit 2 ;;
esac
