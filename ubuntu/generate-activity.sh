#!/usr/bin/env bash
# ===========================================================================
# generate-activity.sh - emit correlated demo activity for the Correlation app.
#
# Writes syslog events (via `logger`) that deliberately SHARE services and users
# across boxes, so when you run this on more than one box the Correlation
# dashboards show the same service/user appearing in multiple data sources.
#
# Run on any lab box (ubuntu-london, ubuntu-berlin, and/or the Splunk host):
#
#     curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/generate-activity.sh | bash
#   or with a custom event count:
#     curl -fsSL <url> | bash -s -- 50
#
# No root needed - logger writes to syslog, which rsyslog already forwards to
# the right per-location index. Correlate by "service" or "user" afterwards.
# ===========================================================================
set -euo pipefail

COUNT="${1:-25}"

# Shared across all boxes on purpose -> these correlate across sources.
services=(authsvc paymentsvc orderapi vpn-gw backup)
users=(alice bob carol dave)
actions=(login logout request approve deny sync)

echo "Generating ${COUNT} correlated events on $(hostname)..."
for _ in $(seq 1 "$COUNT"); do
  svc=${services[$((RANDOM % ${#services[@]}))]}
  usr=${users[$((RANDOM % ${#users[@]}))]}
  act=${actions[$((RANDOM % ${#actions[@]}))]}
  ip="10.0.$((RANDOM % 50)).$((RANDOM % 254 + 1))"
  # -t sets the syslog tag (Splunk sees it as the "service"); the message
  # carries user=... so correlation by user also works.
  logger -t "$svc" "user=${usr} action=${act} src=${ip} status=ok id=${RANDOM}"
  sleep 0.15
done

echo "Done. In Splunk: index=* \"user=alice\"  or open the Correlation app."
echo "Run this on ubuntu-london AND ubuntu-berlin (and the Splunk host) to see"
echo "the same services/users correlate across 2 or 3 sources."
