#!/usr/bin/env bash
# ===========================================================================
# install-uf.sh - install a Splunk Universal Forwarder on an Ubuntu box and
# have it locally collect data (files + the local Proxmox REST API) and forward
# to the indexer. Demonstrates the "one agent collects everything" architecture.
#
# Run on ubuntu-berlin:
#   curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/install-uf.sh | sudo bash
#
# The UF package URL is pinned below but overridable — if it 404s, grab the
# current UF Linux x86_64 .tgz URL from splunk.com and pass it:
#   ... | sudo SPLUNK_UF_URL='https://download.splunk.com/.../splunkforwarder-XX-Linux-x86_64.tgz' bash
# ===========================================================================
set -euo pipefail

INDEXER="${SPLUNK_INDEXER:-198.18.1.124}"
RECV_PORT="${RECV_PORT:-9997}"
UF_HOME="${SPLUNK_UF_HOME:-/opt/splunkforwarder}"
ADMIN_PW="${SPLUNK_ADMIN_PASSWORD:-C1sco12345}"
REPO="${DCLOUD_REPO:-js-csco/splunk-dcloud}"
BRANCH="${DCLOUD_BRANCH:-main}"
TA="TA-dcloud-proxmox"
# Pinned UF (override with SPLUNK_UF_URL if this version/hash is unavailable).
UF_URL="${SPLUNK_UF_URL:-https://download.splunk.com/products/universalforwarder/releases/9.2.1/linux/splunkforwarder-9.2.1-78803f08aabb-Linux-x86_64.tgz}"

run_root() { if [ "$(id -u)" = "0" ]; then "$@"; else sudo "$@"; fi; }

echo "== Splunk UF on $(hostname) -> ${INDEXER}:${RECV_PORT} =="

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

# 3) deploy the localized Proxmox poller TA from the repo
command -v git >/dev/null 2>&1 || { export DEBIAN_FRONTEND=noninteractive; run_root apt-get update -y && run_root apt-get install -y git; }
work="$(mktemp -d)"
git clone --depth 1 -b "${BRANCH}" "https://github.com/${REPO}.git" "${work}"
run_root rm -rf "${UF_HOME}/etc/apps/${TA}"
run_root cp -a "${work}/splunk/uf-apps/${TA}" "${UF_HOME}/etc/apps/${TA}"
run_root chmod +x "${UF_HOME}/etc/apps/${TA}/bin/"*.sh 2>/dev/null || true
rm -rf "${work}"

# 4) first start (fully non-interactive) or restart; enable boot-start
if ! run_root "${UF_HOME}/bin/splunk" status >/dev/null 2>&1; then
  # Seed the admin account BEFORE the first start so Splunk never prompts for a
  # username/password (user-seed.conf is consumed on first run).
  run_root tee "${UF_HOME}/etc/system/local/user-seed.conf" >/dev/null <<EOF
[user_info]
USERNAME = admin
PASSWORD = ${ADMIN_PW}
EOF
  run_root "${UF_HOME}/bin/splunk" start --accept-license --answer-yes --no-prompt
  run_root "${UF_HOME}/bin/splunk" enable boot-start 2>/dev/null || true
else
  run_root "${UF_HOME}/bin/splunk" restart
fi

echo "Done. UF is forwarding to ${INDEXER}:${RECV_PORT} and TA ${TA} polls the local"
echo "Proxmox API every 60s -> berlin_proxmox."
echo "NOTE: disable the CENTRAL Proxmox poll on the Splunk box to avoid duplicates"
echo "      (get_data_in/local/inputs.conf: [script://./bin/poll_proxmox.py] disabled=1)."
