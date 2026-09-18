#!/usr/bin/env bash
# ===========================================================================
# install-uf-proxmox.sh - install a Splunk Universal Forwarder ON THE PROXMOX
# HOST (Debian) so the hypervisor's own OS telemetry flows into Splunk, in
# addition to the API poll. Collects, with LIVE timestamps:
#   - host metrics (CPU/mem/disk/load) every 60s -> berlin_metrics (linux:metrics)
#   - ALL running processes every 60s            -> berlin_linux   (linux:ps)
#   - systemd services + state every 5 min       -> berlin_linux   (linux:services)
#   - /var/log (syslog, auth, kern, …)           -> berlin_linux   (linux:syslog)
#
# The host id is pinned to proxmox-berlin so it matches the API-poll data, the
# ITSI Proxmox entity, and the reachability probes. From the host, the process
# list also shows the container (LXC) processes that run in the host's tree.
#
# Run ON the Proxmox host as root:
#   curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/install-uf-proxmox.sh | bash
# Optional overrides:
#   ... | HOSTID=proxmox-berlin SPLUNK_INDEXER=198.18.1.124 bash
#   ... | SPLUNK_UF_URL='https://download.splunk.com/.../splunkforwarder-XX-Linux-x86_64.tgz' bash
# ===========================================================================
set -euo pipefail

SITE="berlin"
HOSTID="${HOSTID:-proxmox-berlin}"
INDEXER="${SPLUNK_INDEXER:-198.18.1.124}"
RECV_PORT="${RECV_PORT:-9997}"
UF_HOME="${SPLUNK_UF_HOME:-/opt/splunkforwarder}"
ADMIN_PW="${SPLUNK_ADMIN_PASSWORD:-C1sco12345}"
REPO="${DCLOUD_REPO:-js-csco/splunk-dcloud}"
BRANCH="${DCLOUD_BRANCH:-main}"
METRICS_INDEX="${SITE}_metrics"
LINUX_INDEX="${SITE}_linux"
UF_URL="${SPLUNK_UF_URL:-https://download.splunk.com/products/universalforwarder/releases/9.2.1/linux/splunkforwarder-9.2.1-78803f08aabb-Linux-x86_64.tgz}"

run_root() { if [ "$(id -u)" = "0" ]; then "$@"; else sudo "$@"; fi; }

echo "== Splunk UF on Proxmox host $(hostname) as host=${HOSTID} [site=${SITE}] -> ${INDEXER}:${RECV_PORT} =="

# 1) install the UF if not present
if [ ! -x "${UF_HOME}/bin/splunk" ]; then
  command -v wget >/dev/null 2>&1 || { export DEBIAN_FRONTEND=noninteractive; run_root apt-get update -y && run_root apt-get install -y wget; }
  tmp="$(mktemp /tmp/uf-XXXXXX.tgz)"
  echo "Downloading UF: ${UF_URL}"
  if ! wget -qO "$tmp" "${UF_URL}"; then
    echo "ERROR: UF download failed. Set SPLUNK_UF_URL to the current UF Linux x86_64 .tgz from splunk.com and re-run." >&2
    rm -f "$tmp"; exit 1
  fi
  run_root tar -xzf "$tmp" -C /opt
  rm -f "$tmp"
else
  echo "UF already installed at ${UF_HOME}."
fi

# 2) forward to the indexer
run_root mkdir -p "${UF_HOME}/etc/system/local"
run_root tee "${UF_HOME}/etc/system/local/outputs.conf" >/dev/null <<EOF
[tcpout]
defaultGroup = dcloud_indexers

[tcpout:dcloud_indexers]
server = ${INDEXER}:${RECV_PORT}
EOF

# 3) deploy the host TA (metrics + processes + services + /var/log) from the repo
command -v git >/dev/null 2>&1 || { export DEBIAN_FRONTEND=noninteractive; run_root apt-get update -y && run_root apt-get install -y git; }
work="$(mktemp -d)"
git clone --depth 1 -b "${BRANCH}" "https://github.com/${REPO}.git" "${work}"
run_root rm -rf "${UF_HOME}/etc/apps/TA-dcloud-host"
run_root cp -a "${work}/splunk/uf-apps/TA-dcloud-host" "${UF_HOME}/etc/apps/TA-dcloud-host"
run_root mkdir -p "${UF_HOME}/etc/apps/TA-dcloud-host/local"
run_root tee "${UF_HOME}/etc/apps/TA-dcloud-host/local/inputs.conf" >/dev/null <<EOF
[script://./bin/collect_host_metrics.sh ${SITE}]
index = ${METRICS_INDEX}
sourcetype = linux:metrics
host = ${HOSTID}
interval = 60
disabled = 0

[script://./bin/collect_processes.sh]
index = ${LINUX_INDEX}
sourcetype = linux:ps
host = ${HOSTID}
interval = 60
disabled = 0

[script://./bin/collect_services.sh]
index = ${LINUX_INDEX}
sourcetype = linux:services
host = ${HOSTID}
interval = 300
disabled = 0

[monitor:///var/log]
index = ${LINUX_INDEX}
sourcetype = linux:syslog
host = ${HOSTID}
disabled = 0
whitelist = (syslog|auth\.log|kern\.log|dpkg\.log|daemon\.log|messages|pveproxy|pvedaemon)
EOF
run_root chmod +x "${UF_HOME}/etc/apps/TA-dcloud-host/bin/"*.sh 2>/dev/null || true
rm -rf "${work}"

# 4) accept license, enable boot-start, (re)start
run_root tee "${UF_HOME}/etc/system/local/user-seed.conf" >/dev/null <<EOF
[user_info]
USERNAME = admin
PASSWORD = ${ADMIN_PW}
EOF
run_root "${UF_HOME}/bin/splunk" enable boot-start --accept-license --no-prompt --answer-yes >/dev/null 2>&1 || true
run_root "${UF_HOME}/bin/splunk" restart --accept-license --no-prompt --answer-yes >/dev/null 2>&1 \
  || run_root "${UF_HOME}/bin/splunk" start --accept-license --no-prompt --answer-yes >/dev/null 2>&1 || true

echo "Done. Proxmox host ${HOSTID} is forwarding to ${INDEXER}:${RECV_PORT}:"
echo "  - host metrics   -> ${METRICS_INDEX} (linux:metrics)"
echo "  - all processes  -> ${LINUX_INDEX} (linux:ps)"
echo "  - services       -> ${LINUX_INDEX} (linux:services)"
echo "  - /var/log       -> ${LINUX_INDEX} (linux:syslog)"
echo "Verify:  index=${LINUX_INDEX} host=${HOSTID} sourcetype=linux:services active=active sub=running | stats count"
