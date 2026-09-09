#!/usr/bin/env bash
# ===========================================================================
# install-uf.sh - install a Splunk Universal Forwarder on an Ubuntu box and have
# it locally collect data and forward to the indexer. Demonstrates the "one
# agent collects everything" architecture.
#
# Collects (per site):
#   - host metrics (CPU/mem/disk/load) every 60s   -> <site>_metrics   (metric)
#   - /var/log file monitor                         -> <site>_linux     (events)
#   - Berlin ONLY: local Proxmox REST API every 60s -> berlin_proxmox + berlin_metrics
#
# Run ON the Ubuntu box, passing its site (london | berlin):
#   curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/install-uf.sh | sudo bash -s -- london
#   curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/install-uf.sh | sudo bash -s -- berlin
#
# If the UF package URL 404s, grab the current UF Linux x86_64 .tgz URL from
# splunk.com and pass it:
#   ... | sudo SPLUNK_UF_URL='https://download.splunk.com/.../splunkforwarder-XX-Linux-x86_64.tgz' bash -s -- berlin
# ===========================================================================
set -euo pipefail

# --- site (london|berlin): from arg 1, else $SITE, else infer from hostname ---
SITE="${1:-${SITE:-}}"
if [ -z "${SITE}" ]; then
  case "$(hostname)" in
    *london*) SITE="london" ;;
    *berlin*) SITE="berlin" ;;
    *) echo "ERROR: could not determine site. Pass it: ... | sudo bash -s -- london" >&2; exit 1 ;;
  esac
fi
case "${SITE}" in
  london|berlin) ;;
  *) echo "ERROR: site must be 'london' or 'berlin' (got '${SITE}')." >&2; exit 1 ;;
esac

INDEXER="${SPLUNK_INDEXER:-198.18.1.124}"
RECV_PORT="${RECV_PORT:-9997}"
UF_HOME="${SPLUNK_UF_HOME:-/opt/splunkforwarder}"
ADMIN_PW="${SPLUNK_ADMIN_PASSWORD:-C1sco12345}"
REPO="${DCLOUD_REPO:-js-csco/splunk-dcloud}"
BRANCH="${DCLOUD_BRANCH:-main}"
METRICS_INDEX="${SITE}_metrics"
LINUX_INDEX="${SITE}_linux"
# Pinned UF (override with SPLUNK_UF_URL if this version/hash is unavailable).
UF_URL="${SPLUNK_UF_URL:-https://download.splunk.com/products/universalforwarder/releases/9.2.1/linux/splunkforwarder-9.2.1-78803f08aabb-Linux-x86_64.tgz}"

run_root() { if [ "$(id -u)" = "0" ]; then "$@"; else sudo "$@"; fi; }

echo "== Splunk UF on $(hostname) [site=${SITE}] -> ${INDEXER}:${RECV_PORT} =="

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

# 2) forward to the indexer's receiving port
run_root mkdir -p "${UF_HOME}/etc/system/local"
run_root tee "${UF_HOME}/etc/system/local/outputs.conf" >/dev/null <<EOF
[tcpout]
defaultGroup = dcloud_indexers

[tcpout:dcloud_indexers]
server = ${INDEXER}:${RECV_PORT}
EOF

# 3) deploy the forwarder TAs from the repo
command -v git >/dev/null 2>&1 || { export DEBIAN_FRONTEND=noninteractive; run_root apt-get update -y && run_root apt-get install -y git; }
work="$(mktemp -d)"
git clone --depth 1 -b "${BRANCH}" "https://github.com/${REPO}.git" "${work}"

# 3a) host metrics + /var/log monitor (both sites). local/inputs.conf sets the
#     per-site index and passes <site> as the metrics-script argument.
run_root rm -rf "${UF_HOME}/etc/apps/TA-dcloud-host"
run_root cp -a "${work}/splunk/uf-apps/TA-dcloud-host" "${UF_HOME}/etc/apps/TA-dcloud-host"
run_root mkdir -p "${UF_HOME}/etc/apps/TA-dcloud-host/local"
# NOTE: host is pinned to "ubuntu-<site>" because both dCloud Ubuntu boxes share
# the OS hostname "ubuntu". Without this, london/berlin events collide under
# host=ubuntu and can't be told apart on dashboards.
run_root tee "${UF_HOME}/etc/apps/TA-dcloud-host/local/inputs.conf" >/dev/null <<EOF
[script://./bin/collect_host_metrics.sh ${SITE}]
index = ${METRICS_INDEX}
sourcetype = linux:metrics
host = ubuntu-${SITE}
interval = 60
disabled = 0

[monitor:///var/log]
index = ${LINUX_INDEX}
sourcetype = linux:syslog
host = ubuntu-${SITE}
disabled = 0
whitelist = (syslog|auth\.log|kern\.log|dpkg\.log|ufw\.log|messages)$
EOF
run_root chmod +x "${UF_HOME}/etc/apps/TA-dcloud-host/bin/"*.sh 2>/dev/null || true

# 3b) Berlin only: localized Proxmox poller (REST + metrics)
if [ "${SITE}" = "berlin" ]; then
  run_root rm -rf "${UF_HOME}/etc/apps/TA-dcloud-proxmox"
  run_root cp -a "${work}/splunk/uf-apps/TA-dcloud-proxmox" "${UF_HOME}/etc/apps/TA-dcloud-proxmox"
  run_root chmod +x "${UF_HOME}/etc/apps/TA-dcloud-proxmox/bin/"*.sh 2>/dev/null || true
  echo "Berlin: Proxmox poller deployed (berlin_proxmox + berlin_metrics)."
else
  # Never leave a stale Proxmox poller on the London box.
  run_root rm -rf "${UF_HOME}/etc/apps/TA-dcloud-proxmox" 2>/dev/null || true
fi
rm -rf "${work}"

# 4) first start (fully non-interactive) or restart; enable boot-start
#
# IMPORTANT: on a never-initialized instance, the FIRST `splunk` command of ANY
# kind (even `splunk status`) triggers the first-time-run prompts:
#   Do you agree with this license? [y/n]
#   Please enter an administrator username / password
# Splunk writes those to /dev/tty, so redirection and `curl | bash` do NOT
# suppress them. So we must NOT run any splunk command before the license is
# accepted and the admin is seeded. We detect "already initialized" via a
# filesystem marker (etc/passwd, written only after FTR completes) instead of
# `splunk status`.
if [ ! -f "${UF_HOME}/etc/passwd" ]; then
  # Fresh install. Seed the admin BEFORE the first start (user-seed.conf is
  # consumed on first run), then start with --accept-license --no-prompt so no
  # prompt is ever reached.
  run_root tee "${UF_HOME}/etc/system/local/user-seed.conf" >/dev/null <<EOF
[user_info]
USERNAME = admin
PASSWORD = ${ADMIN_PW}
EOF
  run_root "${UF_HOME}/bin/splunk" start --accept-license --answer-yes --no-prompt
  run_root "${UF_HOME}/bin/splunk" enable boot-start --accept-license --answer-yes --no-prompt 2>/dev/null || true
else
  # Already initialized (license accepted, admin exists): a restart is safe and
  # non-interactive, and picks up the freshly deployed inputs.conf.
  run_root "${UF_HOME}/bin/splunk" restart --accept-license --answer-yes --no-prompt
fi

echo "Done. UF on ${SITE} is forwarding to ${INDEXER}:${RECV_PORT}:"
echo "  - host metrics  -> ${METRICS_INDEX}   (Host Metrics dashboard)"
echo "  - /var/log      -> ${LINUX_INDEX}"
[ "${SITE}" = "berlin" ] && echo "  - Proxmox API   -> berlin_proxmox + berlin_metrics (every 60s)"
