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

# The docker-compose mounts several config files by ABSOLUTE path; if any of
# these vars is empty the compose fails with "empty section between colons".
# Point them all at files we create in this docker_compose dir.
setenv SCHEDULER_CONFIG_FILE_ABSOLUTE_PATH "${CD}/scheduler-config.yaml"
setenv TRAPS_CONFIG_FILE_ABSOLUTE_PATH     "${CD}/traps-config.yaml"
setenv DISCOVERY_CONFIG_FILE_ABSOLUTE_PATH "${CD}/discovery-config.yaml"
setenv INVENTORY_FILE_ABSOLUTE_PATH        "${CD}/inventory.csv"
setenv COREFILE_ABS_PATH                   "${CD}/Corefile"

# 4) config files ----------------------------------------------------------
# 4a) inventory - poll the Proxmox host (v2c, our community). Add rows for more
#     devices later. Columns are the SC4SNMP inventory schema.
run_root tee inventory.csv >/dev/null <<CSV
address,port,version,community,secret,security_engine,walk_interval,profiles,smart_profiles,delete
${PROXMOX},161,2c,${COMMUNITY},,,60,,,
CSV

# 4b) traps config - accept SNMPv2c traps using our community (this is what the
#     demo-chaos traps use). Known-good, simple schema.
run_root tee traps-config.yaml >/dev/null <<YAML
communities:
  2c:
    - ${COMMUNITY}
    - public
usernameSecrets: []
YAML

# 4c) scheduler + discovery configs - minimal valid docs (defaults). Polling
#     tuning can be added later; the trap path does not depend on these.
run_root tee scheduler-config.yaml >/dev/null <<'YAML'
# Minimal SC4SNMP scheduler/poller config - defaults are fine for the lab.
poller: {}
scheduler: {}
worker: {}
YAML
run_root tee discovery-config.yaml >/dev/null <<'YAML'
# Minimal discovery config (defaults).
YAML

# 4d) Corefile ships in the repo; if the pinned ref lacks it, create a simple one.
[ -f Corefile ] || run_root tee Corefile >/dev/null <<'COREDNS'
.:53 {
    log
    errors
    auto
    reload
    forward . 8.8.8.8 1.1.1.1
}
COREDNS

# 5) start the stack
run_root docker compose --env-file .env up -d
echo ""
echo "SC4SNMP is starting."
echo "  polling : ${PROXMOX} (community '${COMMUNITY}') every 60s"
echo "  traps   : send device traps to ${HOSTNAME:-this host}:162 (UDP)"
echo "  splunk  : HEC ${HEC_HOST}:${HEC_PORT} -> index=${HEC_INDEX}"
echo "  check   : (cd ${CD} && sudo docker compose ps) ; ... logs -f"
