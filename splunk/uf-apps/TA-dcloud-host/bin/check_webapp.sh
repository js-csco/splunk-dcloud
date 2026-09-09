#!/usr/bin/env bash
# ===========================================================================
# check_webapp.sh - synthetic reachability probe for the web-app container.
# Scripted input on the ubuntu-berlin UF: hits http://<target>/healthz and emits
# one key=value line -> index=berlin_web sourcetype=webapp:probe.
#   [script://./bin/check_webapp.sh 198.18.3.50:8080]
# ===========================================================================
set -u
TARGET="${1:-198.18.3.50:8080}"
ts="$(date -u +%FT%TZ)"
start="$(date +%s%3N)"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://${TARGET}/healthz" 2>/dev/null)"
[ -n "$code" ] || code="000"
end="$(date +%s%3N)"
lat=$(( end - start ))
reachable=0; [ "$code" = "200" ] && reachable=1
printf 'metric_ts=%s source=webapp_probe site=berlin target=%s reachable=%s http_status=%s latency_ms=%s\n' \
  "$ts" "$TARGET" "$reachable" "$code" "$lat"
