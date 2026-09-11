#!/usr/bin/env bash
# ===========================================================================
# deploy-db.sh - run INSIDE the LXC container (db-berlin).
#
# 1) installs PostgreSQL, creates db 'demo' + role 'demo' + table 'entries',
#    and allows connections from the Berlin subnet (so the web-app can write).
# 2) turns on connection + statement logging so writes are visible in Splunk.
# 3) installs a Universal Forwarder that ships:
#      - the PostgreSQL log      -> berlin_db      (sourcetype postgres:log)
#      - host metrics (CPU/mem)  -> berlin_metrics (sourcetype linux:metrics)
#    both stamped host=db-berlin.
#
# Invoked by ubuntu/create-db-container.sh, or by hand inside the container:
#   curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/webapp/deploy-db.sh | bash
# ===========================================================================
set -euo pipefail

REPO="${DCLOUD_REPO:-js-csco/splunk-dcloud}"; BRANCH="${DCLOUD_BRANCH:-main}"
RAW="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
INDEXER="${SPLUNK_INDEXER:-198.18.1.124}"
RECV_PORT="${RECV_PORT:-9997}"
UF_HOME="${SPLUNK_UF_HOME:-/opt/splunkforwarder}"
ADMIN_PW="${SPLUNK_ADMIN_PASSWORD:-C1sco12345}"
HOSTLABEL="${DB_HOST_LABEL:-db-berlin}"
DB_INDEX="${DB_INDEX:-berlin_db}"
METRICS_INDEX="${DB_METRICS_INDEX:-berlin_metrics}"
DB_NAME="${DB_NAME:-demo}"; DB_USER="${DB_USER:-demo}"; DB_PASS="${DB_PASS:-C1sco12345}"
SUBNET="${DB_ALLOW_SUBNET:-198.18.3.0/24}"
UF_URL="${SPLUNK_UF_URL:-https://download.splunk.com/products/universalforwarder/releases/9.2.1/linux/splunkforwarder-9.2.1-78803f08aabb-Linux-x86_64.tgz}"

echo "== deploy-db on $(hostname) (label=${HOSTLABEL}) =="
export DEBIAN_FRONTEND=noninteractive
command -v curl >/dev/null 2>&1 || { apt-get update -y && apt-get install -y curl; }
command -v wget >/dev/null 2>&1 || { apt-get update -y && apt-get install -y wget; }

# --- 1) PostgreSQL ---------------------------------------------------------
command -v psql >/dev/null 2>&1 || { apt-get update -y && apt-get install -y postgresql; }
PGCONF_DIR="$(ls -d /etc/postgresql/*/main 2>/dev/null | head -1)"
PGLOG_GLOB="/var/log/postgresql/postgresql-*-main.log"

# listen on all interfaces + log connections and data-changing statements
if [ -n "${PGCONF_DIR}" ]; then
  sed -i "s/^#\?listen_addresses.*/listen_addresses = '*'/" "${PGCONF_DIR}/postgresql.conf"
  {
    echo "log_connections = on"
    echo "log_disconnections = on"
    echo "log_statement = 'mod'"
    echo "log_line_prefix = '%m [%p] %u@%d %r '"
  } >> "${PGCONF_DIR}/postgresql.conf"
  # allow the Berlin subnet to connect as our demo user (md5)
  grep -q "dcloud-demo" "${PGCONF_DIR}/pg_hba.conf" 2>/dev/null || \
    printf '# dcloud-demo\nhost    %s    %s    %s    md5\n' "${DB_NAME}" "${DB_USER}" "${SUBNET}" >> "${PGCONF_DIR}/pg_hba.conf"
fi

systemctl enable postgresql >/dev/null 2>&1 || true
systemctl restart postgresql

# role + db + table (idempotent)
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='${DB_USER}') THEN
    CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
  END IF;
END \$\$;
SELECT 'ok';
SQL
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 || \
  sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"
sudo -u postgres psql -d "${DB_NAME}" -v ON_ERROR_STOP=1 <<SQL
CREATE TABLE IF NOT EXISTS entries (
  id serial PRIMARY KEY,
  ts timestamptz DEFAULT now(),
  src_ip text, name text, note text, user_agent text
);
ALTER TABLE entries OWNER TO ${DB_USER};
SQL
echo "PostgreSQL ready: db=${DB_NAME} user=${DB_USER}, table 'entries'."

# --- 2) Universal Forwarder ------------------------------------------------
if [ ! -x "${UF_HOME}/bin/splunk" ]; then
  tmp="$(mktemp /tmp/uf-XXXXXX.tgz)"
  echo "Downloading UF: ${UF_URL}"
  wget -qO "$tmp" "${UF_URL}" || { echo "ERROR: UF download failed; set SPLUNK_UF_URL." >&2; rm -f "$tmp"; exit 1; }
  tar -xzf "$tmp" -C /opt; rm -f "$tmp"
fi

mkdir -p "${UF_HOME}/etc/system/local"
cat > "${UF_HOME}/etc/system/local/outputs.conf" <<EOF
[tcpout]
defaultGroup = dcloud_indexers

[tcpout:dcloud_indexers]
server = ${INDEXER}:${RECV_PORT}
EOF

TA="${UF_HOME}/etc/apps/TA-dcloud-db"
mkdir -p "${TA}/bin" "${TA}/local"
curl -fsSL "${RAW}/splunk/apps/infra_monitoring/bin/collect_host_metrics.sh" -o "${TA}/bin/collect_host_metrics.sh"
curl -fsSL "${RAW}/splunk/uf-apps/TA-dcloud-host/bin/collect_processes.sh" -o "${TA}/bin/collect_processes.sh"
chmod +x "${TA}/bin/"*.sh
cat > "${TA}/local/inputs.conf" <<EOF
[monitor://${PGLOG_GLOB}]
index = ${DB_INDEX}
sourcetype = postgres:log
host = ${HOSTLABEL}
disabled = 0

[script://./bin/collect_host_metrics.sh berlin]
index = ${METRICS_INDEX}
sourcetype = linux:metrics
host = ${HOSTLABEL}
interval = 60
disabled = 0

[script://./bin/collect_processes.sh]
index = ${DB_INDEX}
sourcetype = linux:ps
host = ${HOSTLABEL}
interval = 60
disabled = 0
EOF

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

echo "Done. ${HOSTLABEL}: PostgreSQL on :5432; UF -> ${INDEXER}:${RECV_PORT}"
echo "  postgres log -> ${DB_INDEX} (postgres:log), metrics -> ${METRICS_INDEX}"
