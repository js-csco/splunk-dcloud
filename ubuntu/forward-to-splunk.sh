#!/usr/bin/env bash
# ===========================================================================
# forward-to-splunk.sh - point an Ubuntu box's logs at the dCloud Splunk indexer.
#
# Uses rsyslog (built into Ubuntu) to forward all syslog over TCP to a
# per-location port on the indexer, so data lands in the correct location
# index and RBAC is preserved:
#
#     London (loc2, 198.18.2.x)  -> port 5514 -> index london_linux  (role_london, role_global)
#     Berlin (loc3, 198.18.3.x)  -> port 5515 -> index berlin_linux  (role_berlin, role_global)
#     loc1   (Splunk host)       -> port 5513 -> index loc1_linux    (role_global)
#
# Run it on the Ubuntu VM (location auto-detected from hostname or IP):
#
#     curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/forward-to-splunk.sh | sudo bash
#   or force it (accepts london|berlin|loc1, or the loc2/loc3 aliases):
#     curl -fsSL <url> | sudo bash -s -- london
#
# Re-runnable and idempotent. No downloads, no DNS dependency (forwards to an IP).
# ===========================================================================
set -euo pipefail

INDEXER="${SPLUNK_INDEXER:-198.18.1.124}"
LOC="${1:-auto}"

# Normalise aliases (loc2->london, loc3->berlin).
case "$LOC" in
  loc2) LOC="london" ;;
  loc3) LOC="berlin" ;;
esac

if [ "$LOC" = "auto" ]; then
  # 1) try the hostname
  case "$(hostname)" in
    *london*|*loc2*) LOC="london" ;;
    *berlin*|*loc3*) LOC="berlin" ;;
    *loc1*)          LOC="loc1" ;;
  esac
  # 2) fall back to the box's IP subnet (reliable when hostname is generic)
  if [ "$LOC" = "auto" ]; then
    for ip in $(hostname -I 2>/dev/null); do
      case "$ip" in
        198.18.2.*) LOC="london"; break ;;
        198.18.3.*) LOC="berlin"; break ;;
        198.18.1.*) LOC="loc1";   break ;;
      esac
    done
  fi
  if [ "$LOC" = "auto" ]; then
    echo "ERROR: could not detect location from hostname '$(hostname)' or IP ($(hostname -I 2>/dev/null))." >&2
    echo "       Pass it explicitly, e.g.:  curl -fsSL <url> | sudo bash -s -- london" >&2
    exit 1
  fi
fi

case "$LOC" in
  london) PORT=5514; INDEX="london_linux" ;;
  berlin) PORT=5515; INDEX="berlin_linux" ;;
  loc1)   PORT=5513; INDEX="loc1_linux" ;;
  *) echo "ERROR: unknown location '$LOC' (expected london|berlin|loc1)" >&2; exit 1 ;;
esac

run_root() { if [ "$(id -u)" = "0" ]; then "$@"; else sudo "$@"; fi; }

echo "== Forwarding $(hostname) [$LOC] -> ${INDEXER}:${PORT} (index ${INDEX}) =="

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

echo "Done. Verify in Splunk:  index=${INDEX} host=$(hostname)"
echo "(Infrastructure Monitoring -> Data Onboarding Overview should show this host.)"
