#!/usr/bin/env bash
# ===========================================================================
# deploy-webapp.sh - run INSIDE the LXC container (webapp-berlin).
#
# 1) installs the demo web-app (webapp/app.py) as a systemd service on :8080
# 2) installs a Universal Forwarder that ships:
#      - /var/log/webapp/access.log  -> berlin_web  (sourcetype webapp:access)
#      - host metrics (CPU/mem/disk)  -> berlin_metrics (sourcetype linux:metrics)
#    both stamped host=webapp-berlin so the container is distinct in Splunk.
#
# Typically invoked automatically by ubuntu/create-webapp-container.sh, but you
# can also run it by hand inside the container:
#   curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/webapp/deploy-webapp.sh | bash
#
# Needs outbound internet in the container (GitHub + splunk.com for the UF). If
# the UF download is blocked, set SPLUNK_UF_URL to a reachable mirror, or fall
# back to rsyslog (see README).
# ===========================================================================
set -euo pipefail

REPO="${DCLOUD_REPO:-js-csco/splunk-dcloud}"
BRANCH="${DCLOUD_BRANCH:-main}"
RAW="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
INDEXER="${SPLUNK_INDEXER:-198.18.1.124}"
RECV_PORT="${RECV_PORT:-9997}"
UF_HOME="${SPLUNK_UF_HOME:-/opt/splunkforwarder}"
ADMIN_PW="${SPLUNK_ADMIN_PASSWORD:-C1sco12345}"
HOSTLABEL="${WEBAPP_HOST:-webapp-berlin}"
WEB_INDEX="${WEBAPP_INDEX:-berlin_web}"
METRICS_INDEX="${WEBAPP_METRICS_INDEX:-berlin_metrics}"
PORT="${WEBAPP_PORT:-8080}"
UF_URL="${SPLUNK_UF_URL:-https://download.splunk.com/products/universalforwarder/releases/9.2.1/linux/splunkforwarder-9.2.1-78803f08aabb-Linux-x86_64.tgz}"

echo "== deploy-webapp on $(hostname) (label=${HOSTLABEL}) =="

# --- prerequisites ---------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
command -v python3 >/dev/null 2>&1 || { apt-get update -y && apt-get install -y python3; }
command -v curl    >/dev/null 2>&1 || { apt-get update -y && apt-get install -y curl; }
command -v wget    >/dev/null 2>&1 || { apt-get update -y && apt-get install -y wget; }

# --- 1) the web-app --------------------------------------------------------
mkdir -p /opt/webapp /var/log/webapp
curl -fsSL "${RAW}/webapp/app.py" -o /opt/webapp/app.py
cat > /etc/systemd/system/webapp.service <<EOF
[Unit]
Description=dCloud demo web-app
After=network-online.target

[Service]
Environment=WEBAPP_PORT=${PORT}
Environment=WEBAPP_LOG=/var/log/webapp/access.log
ExecStart=/usr/bin/python3 /opt/webapp/app.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now webapp.service
echo "web-app started on :${PORT} (systemctl status webapp)"

# --- 2) Universal Forwarder ------------------------------------------------
if [ ! -x "${UF_HOME}/bin/splunk" ]; then
  tmp="$(mktemp /tmp/uf-XXXXXX.tgz)"
  echo "Downloading UF: ${UF_URL}"
  if ! wget -qO "$tmp" "${UF_URL}"; then
    echo "ERROR: UF download failed. Set SPLUNK_UF_URL to a reachable .tgz, or use the rsyslog fallback (README)." >&2
    rm -f "$tmp"; exit 1
  fi
  tar -xzf "$tmp" -C /opt; rm -f "$tmp"
fi

mkdir -p "${UF_HOME}/etc/system/local"
cat > "${UF_HOME}/etc/system/local/outputs.conf" <<EOF
[tcpout]
defaultGroup = dcloud_indexers

[tcpout:dcloud_indexers]
server = ${INDEXER}:${RECV_PORT}
EOF

# TA: forward the web-app access log + host metrics, stamped host=webapp-berlin
TA="${UF_HOME}/etc/apps/TA-dcloud-webapp"
mkdir -p "${TA}/bin" "${TA}/local"
curl -fsSL "${RAW}/splunk/apps/metrics/bin/collect_host_metrics.sh" -o "${TA}/bin/collect_host_metrics.sh"
chmod +x "${TA}/bin/collect_host_metrics.sh"
cat > "${TA}/local/inputs.conf" <<EOF
[monitor:///var/log/webapp/access.log]
index = ${WEB_INDEX}
sourcetype = webapp:access
host = ${HOSTLABEL}
disabled = 0

[script://./bin/collect_host_metrics.sh berlin]
index = ${METRICS_INDEX}
sourcetype = linux:metrics
host = ${HOSTLABEL}
interval = 60
disabled = 0
EOF

# First start, fully non-interactive (see install-uf.sh for the rationale).
if [ ! -f "${UF_HOME}/etc/passwd" ]; then
  cat > "${UF_HOME}/etc/system/local/user-seed.conf" <<EOF
[user_info]
USERNAME = admin
PASSWORD = ${ADMIN_PW}
EOF
  "${UF_HOME}/bin/splunk" start --accept-license --answer-yes --no-prompt
  "${UF_HOME}/bin/splunk" enable boot-start --accept-license --answer-yes --no-prompt 2>/dev/null || true
else
  "${UF_HOME}/bin/splunk" restart --accept-license --answer-yes --no-prompt
fi

echo "Done. ${HOSTLABEL}: web-app on :${PORT}; UF -> ${INDEXER}:${RECV_PORT}"
echo "  access log -> ${WEB_INDEX} (webapp:access), metrics -> ${METRICS_INDEX}"
