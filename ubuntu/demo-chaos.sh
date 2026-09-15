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

# SNMP trap: PUSH the event the instant it happens (vs waiting for the poll/
# heartbeat to notice). Sent to the SC4SNMP trap receiver -> index berlin_snmp
# -> the "SNMP trap received -> Webex" alert fires. Set SEND_TRAP=0 to disable.
SEND_TRAP="${SEND_TRAP:-1}"
SNMP_TRAP_TARGET="${SNMP_TRAP_TARGET:-198.18.3.52}"   # ubuntu-berlin-snmp (SC4SNMP)
SNMP_COMMUNITY="${SNMP_COMMUNITY:-dcloud}"

run_root() { if [ "$(id -u)" = "0" ]; then "$@"; else sudo "$@"; fi; }

send_trap() { # <label> <STATE>
  [ "${SEND_TRAP}" = "1" ] || return 0
  command -v snmptrap >/dev/null 2>&1 || { export DEBIAN_FRONTEND=noninteractive; \
    run_root apt-get update -y >/dev/null 2>&1 && run_root apt-get install -y snmp >/dev/null 2>&1 || true; }
  command -v snmptrap >/dev/null 2>&1 || { echo "   (snmptrap not installed - skipping trap)"; return 0; }
  local msg="dCloud ${1} container is ${2}"
  # Use linkDown (…5.3) for DOWN, linkUp (…5.4) for UP as the notification type,
  # and carry the details in standard DisplayString OIDs so SC4SNMP decodes them
  # into readable fields:
  #   sysName.0     (1.3.6.1.2.1.1.5.0) -> the affected component
  #   sysDescr.0    (1.3.6.1.2.1.1.1.0) -> the human message
  #   sysLocation.0 (1.3.6.1.2.1.1.6.0) -> where it lives
  local trapoid="1.3.6.1.6.3.1.1.5.3"           # linkDown
  [ "${2}" = "UP" ] && trapoid="1.3.6.1.6.3.1.1.5.4"   # linkUp
  if snmptrap -v2c -c "${SNMP_COMMUNITY}" "${SNMP_TRAP_TARGET}:162" '' "${trapoid}" \
       1.3.6.1.2.1.1.5.0 s "${1}" \
       1.3.6.1.2.1.1.1.0 s "${msg}" \
       1.3.6.1.2.1.1.6.0 s "Berlin / Directory App" 2>/dev/null; then
    echo "   -> SNMP trap PUSHED to ${SNMP_TRAP_TARGET}:162  (\"${msg}\")"
  else
    echo "   (trap send failed - is SC4SNMP up on ${SNMP_TRAP_TARGET}? traps use UDP 162)"
  fi
}

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
    send_trap "$2" "DOWN"
  else
    missing_note "$1" "stop"
  fi
}
start_ct() { # <vmid> <label>
  if ct_exists "$1"; then
    remote "pct start ${1}" || true
    send_trap "$2" "UP"
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

Watch the effect in Splunk:
  * INSTANT (push): the SNMP trap is in index=berlin_snmp within seconds; the
    "dcloud - SNMP trap received -> Webex" alert fires. This is the real-time signal.
  * ~2-5 min (poll): ITSI Service Analyzer turns "Web/Database Service" + the
    "Global IT Operations" rollup red as the heartbeat gap is detected.
  * Correlation -> Root Cause Analysis pinpoints the down layer.
Recover with:  demo-chaos.sh recover     (sends an "UP" trap too)
Note: the trap needs SC4SNMP running on ubuntu-berlin-snmp (snmp/setup-sc4snmp.sh).
EOF
