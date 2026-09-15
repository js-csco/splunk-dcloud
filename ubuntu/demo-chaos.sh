#!/usr/bin/env bash
# ===========================================================================
# demo-chaos.sh - break & recover lab containers to demo Splunk + ITSI live.
#
# The money-shot demo: stop a container, then watch (within a few minutes):
#   * the ITSI service go RED (App/DB Reachability KPI - Service Analyzer),
#   * the Correlation -> Root Cause Analysis dashboard pinpoint the layer,
#   * the "Service unavailable -> Webex" alert fire (enable it first).
# Then 'recover' and watch everything go green again.
#
# Run it EITHER:
#   * directly on the Proxmox host as root (recommended - runs pct locally):
#       curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/demo-chaos.sh | bash -s -- <action>
#     or, if the repo is already cloned there:  bash ubuntu/demo-chaos.sh <action>
#   * OR on ubuntu-berlin (SSHes to Proxmox; that box has sudo):
#       curl -fsSL .../demo-chaos.sh | sudo bash -s -- <action>
#
# Actions:
#   break-web    stop the web-app container (VMID 200)
#   break-db     stop the PostgreSQL container (VMID 201)
#   break-all    stop both
#   recover      start both again
#   status       show current status of both containers
#
# Overridable via env: PROXMOX_HOST, PROXMOX_PW, WEB_VMID, DB_VMID.
# ===========================================================================
set -euo pipefail

PROX="${PROXMOX_HOST:-198.18.3.11}"
PPW="${PROXMOX_PW:-C1sco12345}"
WEB_VMID="${WEB_VMID:-200}"
DB_VMID="${DB_VMID:-201}"
ACTION="${1:-status}"

run_root() { if [ "$(id -u)" = "0" ]; then "$@"; else sudo "$@"; fi; }

# Run pct locally on the Proxmox host, or over SSH from elsewhere.
if command -v pct >/dev/null 2>&1; then
  remote() { bash -c "$*"; }
else
  command -v sshpass >/dev/null 2>&1 || { export DEBIAN_FRONTEND=noninteractive; run_root apt-get update -y && run_root apt-get install -y sshpass; }
  SSH=(sshpass -p "${PPW}" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 "root@${PROX}")
  if ! "${SSH[@]}" true 2>/dev/null; then
    echo "ERROR: cannot SSH to Proxmox at ${PROX}. Run this on the Proxmox host, or check creds (root/${PPW})." >&2
    exit 1
  fi
  remote() { "${SSH[@]}" "$@"; }
fi

ct_exists() { remote "pct config ${1}" >/dev/null 2>&1; }

# Warn clearly if a container isn't there (it was never created, or got wiped) -
# stopping/starting a missing VMID is otherwise a confusing silent no-op.
missing_note() {
  cat >&2 <<EOF
!! Container VMID ${1} does not exist on this Proxmox node.
   Nothing to ${2}. (Re)create the lab containers first, on the Proxmox host:
     curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/create-webapp-container.sh | bash
     curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/create-db-container.sh | bash
EOF
}

break_ct() { # <vmid> <label>
  if ct_exists "$1"; then
    echo "== CHAOS: stopping ${2} container (VMID ${1}) =="
    remote "pct stop ${1}" || true
  else
    missing_note "$1" "stop"
  fi
}
start_ct() { # <vmid> <label>
  if ct_exists "$1"; then
    remote "pct start ${1}" || true
  else
    missing_note "$1" "start"
  fi
}

show_status() {
  for pair in "web-app:${WEB_VMID}" "database:${DB_VMID}"; do
    name="${pair%%:*}"; vmid="${pair##*:}"
    if ct_exists "${vmid}"; then
      st="$(remote "pct status ${vmid}" 2>/dev/null | awk '{print $2}')"
    else
      st="NOT CREATED"
    fi
    printf '  %-9s (VMID %s): %s\n' "${name}" "${vmid}" "${st:-unknown}"
  done
}

case "${ACTION}" in
  break-web)  break_ct "${WEB_VMID}" "web-app" ;;
  break-db)   break_ct "${DB_VMID}"  "database" ;;
  break-all)  break_ct "${WEB_VMID}" "web-app"; break_ct "${DB_VMID}" "database" ;;
  recover)
    echo "== RECOVER: starting web-app + database containers =="
    start_ct "${WEB_VMID}" "web-app"; start_ct "${DB_VMID}" "database" ;;
  status) : ;;
  *)
    echo "Usage: demo-chaos.sh <break-web|break-db|break-all|recover|status>" >&2
    exit 2 ;;
esac

echo "Current container status:"
show_status
cat <<'EOF'

Watch the effect in Splunk (allow ~2-5 min for KPIs/probes to catch up):
  * ITSI  -> Service Analyzer: "Web Service (Berlin)" / "Database Service (Berlin)"
            and the "Global IT Operations" rollup turn red.
  * Splunk -> Correlation app -> Root Cause Analysis: pinpoints the down layer.
  * Alerts -> enable "dcloud - Service unavailable -> Webex" to get a Webex ping.
Recover with:  demo-chaos.sh recover
EOF
