#!/usr/bin/env bash
# ===========================================================================
# install-uf-desktop.sh - install a Splunk Universal Forwarder on an Ubuntu
# 24.04 DESKTOP acting as the end-user CLIENT in the Client -> App -> Hypervisor
# demo (replaces the Windows client). Collects, all with LIVE timestamps:
#   - host metrics (CPU/mem/disk/load) every 60s   -> london_metrics (linux:metrics)
#   - logged-on users every 60s                    -> london_linux   (linux:sessions)
#   - top processes by CPU every 60s               -> london_linux   (linux:ps)
#   - /var/log (auth.log logins, syslog, etc.)     -> london_linux   (linux:syslog)
#
# The client gets a DISTINCT host id (default desktop-london) so it doesn't
# collide with the infra box ubuntu-london. The correlation dashboard's Client
# node filters host=desktop-london.
#
# Run ON the Ubuntu desktop:
#   curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/install-uf-desktop.sh | sudo bash
# Optional overrides:
#   ... | sudo HOSTID=desktop-london SITE=london bash
#   ... | sudo SPLUNK_UF_URL='https://download.splunk.com/.../splunkforwarder-XX-Linux-x86_64.tgz' bash
# ===========================================================================
set -euo pipefail

SITE="${SITE:-london}"
HOSTID="${HOSTID:-desktop-${SITE}}"
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

echo "== Splunk UF on $(hostname) as CLIENT host=${HOSTID} [site=${SITE}] -> ${INDEXER}:${RECV_PORT} =="

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

# 3) deploy the host TA (metrics + sessions + processes + /var/log) from the repo
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

[script://./bin/collect_sessions.sh]
index = ${LINUX_INDEX}
sourcetype = linux:sessions
host = ${HOSTID}
interval = 60
disabled = 0

[script://./bin/collect_processes.sh]
index = ${LINUX_INDEX}
sourcetype = linux:ps
host = ${HOSTID}
interval = 60
disabled = 0

[monitor:///var/log]
index = ${LINUX_INDEX}
sourcetype = linux:syslog
host = ${HOSTID}
disabled = 0
whitelist = (syslog|auth\.log|kern\.log|dpkg\.log|ufw\.log|messages)$
EOF
run_root chmod +x "${UF_HOME}/etc/apps/TA-dcloud-host/bin/"*.sh 2>/dev/null || true
# A client desktop never runs the Proxmox poller.
run_root rm -rf "${UF_HOME}/etc/apps/TA-dcloud-proxmox" 2>/dev/null || true
rm -rf "${work}"

# 4) first start (non-interactive) or restart; enable boot-start. See install-uf.sh
#    for why we must seed the admin BEFORE any splunk command on a fresh install.
if [ ! -f "${UF_HOME}/etc/passwd" ]; then
  run_root tee "${UF_HOME}/etc/system/local/user-seed.conf" >/dev/null <<EOF
[user_info]
USERNAME = admin
PASSWORD = ${ADMIN_PW}
EOF
  run_root "${UF_HOME}/bin/splunk" start --accept-license --answer-yes --no-prompt
  run_root "${UF_HOME}/bin/splunk" enable boot-start --accept-license --answer-yes --no-prompt 2>/dev/null || true
else
  run_root "${UF_HOME}/bin/splunk" restart --accept-license --answer-yes --no-prompt
fi

echo "Done. Client ${HOSTID} is forwarding to ${INDEXER}:${RECV_PORT}:"
echo "  - host metrics   -> ${METRICS_INDEX} (linux:metrics)"
echo "  - logged-on users-> ${LINUX_INDEX} (linux:sessions)"
echo "  - top processes  -> ${LINUX_INDEX} (linux:ps)"
echo "  - /var/log       -> ${LINUX_INDEX} (linux:syslog)"
echo "Generate a login event any time:  logger -p auth.info \"demo login by \$USER\""
