#!/usr/bin/env bash
# ===========================================================================
# setup-sc4snmp.sh - stand up Splunk Connect for SNMP (SC4SNMP) via Docker
# Compose on the dedicated Berlin VM (ubuntu-berlin-snmp, 198.18.3.52).
#
# SC4SNMP is the current, Splunk-supported way to do SNMP. It's a small
# microservice stack (Mongo, Redis, workers, scheduler, a trap receiver and a
# sender) deployed with Docker Compose. It POLLS devices (UDP 161), RECEIVES
# traps (UDP 162), and ships to Splunk over HEC.
#
# Run on ubuntu-berlin-snmp:
#   curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/snmp/setup-sc4snmp.sh | sudo -E bash
#
# HEC token defaults to the fixed lab token that apply.sh configures on Splunk.
# Pin SC4SNMP_REF to a released tag for a stable build (e.g. SC4SNMP_REF=v1.12.0).
#
# NOTE: SC4SNMP's compose layout and .env keys evolve between versions. This
# script clones the OFFICIAL repo and only overrides our values, so it inherits
# the upstream structure - but on first run confirm against the cloned
# docker_compose/.env of the ref you pinned.
# ===========================================================================
set -euo pipefail

HEC_HOST="${HEC_HOST:-198.18.1.124}"
HEC_PORT="${HEC_PORT:-8088}"
HEC_TOKEN="${HEC_TOKEN:-d1c0feed-5abc-4a1b-9c2d-000000000001}"
HEC_INDEX="${HEC_INDEX:-berlin_snmp}"
PROXMOX="${PROXMOX_HOST:-198.18.3.11}"
COMMUNITY="${SNMP_COMMUNITY:-dcloud}"
SC4SNMP_REF="${SC4SNMP_REF:-main}"
DIR="${SC4SNMP_DIR:-/opt/sc4snmp}"
run_root() { if [ "$(id -u)" = "0" ]; then "$@"; else sudo "$@"; fi; }

echo "== SC4SNMP on $(hostname) -> HEC ${HEC_HOST}:${HEC_PORT} index=${HEC_INDEX} =="

# 1) Docker + compose plugin
if ! command -v docker >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  run_root apt-get update -y
  run_root apt-get install -y docker.io docker-compose-v2 git
  run_root systemctl enable --now docker
fi

# 2) fetch the official SC4SNMP docker-compose bundle
run_root mkdir -p "${DIR}"
if [ ! -d "${DIR}/repo/.git" ]; then
  run_root git clone --depth 1 -b "${SC4SNMP_REF}" https://github.com/splunk/splunk-connect-for-snmp "${DIR}/repo"
fi
CD="${DIR}/repo/docker_compose"
[ -d "${CD}" ] || { echo "ERROR: ${CD} not found for ref '${SC4SNMP_REF}'. Pick a tag whose repo ships docker_compose/ and re-run with SC4SNMP_REF=<tag>." >&2; exit 1; }
cd "${CD}"

# 3) template .env - inherit upstream template, override only our values
[ -f .env ] || { [ -f .env.example ] && run_root cp .env.example .env; } || run_root touch .env
setenv() { k="$1"; v="$2"; if grep -q "^${k}=" .env 2>/dev/null; then run_root sed -i "s|^${k}=.*|${k}=${v}|" .env; else echo "${k}=${v}" | run_root tee -a .env >/dev/null; fi; }
setenv SPLUNK_HEC_HOST "${HEC_HOST}"
setenv SPLUNK_HEC_PORT "${HEC_PORT}"
setenv SPLUNK_HEC_PROTOCOL https
setenv SPLUNK_HEC_TOKEN "${HEC_TOKEN}"
setenv SPLUNK_HEC_INSECURESSL true
setenv SPLUNK_HEC_INDEX_EVENTS "${HEC_INDEX}"
setenv SPLUNK_HEC_INDEX_METRICS "${HEC_INDEX}"
setenv SC4SNMP_VERSION "${SC4SNMP_VERSION:-latest}"

# 4) inventory - poll the Proxmox host (v2c, our community). Add more rows for
#    other devices later. Columns are the SC4SNMP inventory schema.
run_root tee inventory.csv >/dev/null <<CSV
address,port,version,community,secret,security_engine,walk_interval,profiles,smart_profiles,delete
${PROXMOX},161,2c,${COMMUNITY},,,60,,,
CSV

# 5) start the stack
run_root docker compose --env-file .env up -d
echo ""
echo "SC4SNMP is starting."
echo "  polling : ${PROXMOX} (community '${COMMUNITY}') every 60s"
echo "  traps   : send device traps to ${HOSTNAME:-this host}:162 (UDP)"
echo "  splunk  : HEC ${HEC_HOST}:${HEC_PORT} -> index=${HEC_INDEX}"
echo "  check   : (cd ${CD} && sudo docker compose ps) ; ... logs -f"
