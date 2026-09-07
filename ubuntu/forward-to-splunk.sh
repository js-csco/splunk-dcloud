#!/usr/bin/env bash
# ===========================================================================
# forward-to-splunk.sh - point an Ubuntu box's logs at the dCloud Splunk indexer.
#
# Uses rsyslog (built into Ubuntu) to forward all syslog over TCP to a
# per-location port on the indexer, so data lands in the correct location
# index and RBAC is preserved:
#
#     ubuntu-loc2  -> port 5514 -> index loc2_linux   (role_loc2, role_global)
#     ubuntu-loc3  -> port 5515 -> index loc3_linux   (role_global)
#
# Run it on the Ubuntu VM (location auto-detected from the hostname):
#
#     curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/forward-to-splunk.sh | sudo bash
#   or, forcing a location:
#     sudo bash forward-to-splunk.sh loc2
#
# Re-runnable and idempotent. No downloads, no DNS dependency (forwards to an IP).
# ===========================================================================
set -euo pipefail

INDEXER="${SPLUNK_INDEXER:-198.18.1.124}"
LOC="${1:-auto}"

if [ "$LOC" = "auto" ]; then
  host="$(hostname)"
  case "$host" in
    *loc1*) LOC="loc1" ;;
    *loc2*) LOC="loc2" ;;
    *loc3*) LOC="loc3" ;;
    *) echo "ERROR: cannot detect location from hostname '$host'. Pass one: sudo bash $0 loc2" >&2; exit 1 ;;
  esac
fi

case "$LOC" in
  loc1) PORT=5513 ;;
  loc2) PORT=5514 ;;
  loc3) PORT=5515 ;;
  *) echo "ERROR: unknown location '$LOC' (expected loc1|loc2|loc3)" >&2; exit 1 ;;
esac

run_root() { if [ "$(id -u)" = "0" ]; then "$@"; else sudo "$@"; fi; }

echo "== Forwarding $(hostname) [$LOC] -> ${INDEXER}:${PORT} (index ${LOC}_linux) =="

# rsyslog must be present (default on Ubuntu). Install if somehow missing.
if ! command -v rsyslogd >/dev/null 2>&1; then
  echo "rsyslog not found - installing..."
  export DEBIAN_FRONTEND=noninteractive
  run_root apt-get update -y && run_root apt-get install -y rsyslog
fi

CONF="/etc/rsyslog.d/99-splunk-dcloud.conf"
run_root tee "$CONF" >/dev/null <<EOF
# Managed by splunk-dcloud/ubuntu/forward-to-splunk.sh
# Forward all logs to the dCloud Splunk indexer over TCP.
# A disk-assisted queue keeps logs if the indexer is briefly unreachable.
\$ActionQueueType LinkedList
\$ActionQueueFileName splunk_dcloud_fwd
\$ActionResumeRetryCount -1
\$ActionQueueSaveOnShutdown on
*.* @@${INDEXER}:${PORT}
EOF

run_root systemctl restart rsyslog

# Emit a marker event so you can immediately confirm data is arriving.
logger "dcloud-splunk: forwarding enabled from $(hostname) [$LOC] to ${INDEXER}:${PORT}"

echo "Done. Verify in Splunk:  index=${LOC}_linux host=$(hostname)"
echo "(Infrastructure Monitoring app -> Data Onboarding Overview should show this host.)"
