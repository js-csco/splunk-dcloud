#!/usr/bin/env bash
# ===========================================================================
# create-db-container.sh - create the PostgreSQL LXC (db-berlin) on the Berlin
# Proxmox and provision it. Second service for the App tier, so we can show
# "App available" only when BOTH the web-app and the DB container are up.
#
# Run it EITHER:
#   * directly on the Proxmox host as root (recommended - runs pct locally, no
#     SSH; Proxmox is Debian and has NO sudo, so use plain `bash`, not sudo):
#       curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/create-db-container.sh | bash
#   * OR on ubuntu-berlin (SSHes to Proxmox; that box has sudo):
#       curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/create-db-container.sh | sudo bash
#
# Overridable via env: PROXMOX_HOST, PROXMOX_PW, CT_VMID, CT_IP, CT_GW,
# CT_BRIDGE, CT_STORAGE, CT_TEMPLATE.
# ===========================================================================
set -euo pipefail

PROX="${PROXMOX_HOST:-198.18.3.11}"
PPW="${PROXMOX_PW:-C1sco12345}"
VMID="${CT_VMID:-201}"
CTNAME="${CT_HOSTNAME:-db-berlin}"
CT_IP="${CT_IP:-198.18.3.51}"
BRIDGE="${CT_BRIDGE:-vmbr0}"
CT_GW="${CT_GW:-$(ip route 2>/dev/null | awk '/^default/{print $3; exit}')}"
REPO="${DCLOUD_REPO:-js-csco/splunk-dcloud}"; BRANCH="${DCLOUD_BRANCH:-main}"
DEPLOY_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/webapp/deploy-db.sh"

run_root() { if [ "$(id -u)" = "0" ]; then "$@"; else sudo "$@"; fi; }

if command -v pct >/dev/null 2>&1; then
  echo "Detected Proxmox host locally (pct present) - running pct directly, no SSH."
  remote() { bash -c "$*"; }
else
  command -v sshpass >/dev/null 2>&1 || { export DEBIAN_FRONTEND=noninteractive; run_root apt-get update -y && run_root apt-get install -y sshpass; }
  SSH=(sshpass -p "${PPW}" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 "root@${PROX}")
  if ! "${SSH[@]}" true 2>/dev/null; then
    echo "ERROR: cannot SSH to Proxmox at ${PROX}. Check reachability/creds (root/${PPW})." >&2
    exit 1
  fi
  remote() { "${SSH[@]}" "$@"; }
fi

echo "== create PostgreSQL LXC ${VMID} (${CTNAME}) on Proxmox ${PROX}, ip ${CT_IP}, gw ${CT_GW:-?} =="
[ -n "${CT_GW:-}" ] || { echo "ERROR: could not determine gateway; pass CT_GW=..." >&2; exit 1; }

if remote "pct status ${VMID}" >/dev/null 2>&1; then
  echo "Container ${VMID} already exists - ensuring it is running."
  remote "pct start ${VMID} || true"
else
  STORAGE="${CT_STORAGE:-$(remote "pvesm status -content rootdir 2>/dev/null | awk 'NR>1{print \$1; exit}'")}"
  [ -n "${STORAGE:-}" ] || STORAGE="local-lvm"
  remote "pveam update >/dev/null 2>&1 || true"
  TEMPLATE="${CT_TEMPLATE:-$(remote "pveam list local 2>/dev/null | awk '/ubuntu-2[24].*standard/{print \$1; exit}'")}"
  if [ -z "${TEMPLATE:-}" ]; then
    AVAIL="$(remote "pveam available --section system 2>/dev/null | awk '/ubuntu-22.04-standard/{print \$2; exit}'")"
    [ -n "${AVAIL:-}" ] || { echo "ERROR: no ubuntu template available via pveam." >&2; exit 1; }
    echo "Downloading template ${AVAIL} ..."
    remote "pveam download local '${AVAIL}'"
    TEMPLATE="local:vztmpl/${AVAIL}"
  fi
  echo "Using storage=${STORAGE} template=${TEMPLATE}"
  create_ct() {
    remote "pct create ${VMID} '${TEMPLATE}' --hostname ${CTNAME} --cores 1 --memory 1024 --swap 512 \
      --net0 name=eth0,bridge=${BRIDGE},ip=${CT_IP}/24,gw=${CT_GW} \
      --storage $1 --rootfs $1:6 --unprivileged 1 --features nesting=1 \
      --onboot 1 --password '${PPW}' --description 'dCloud demo PostgreSQL (Splunk correlation)'"
  }
  if ! create_ct "${STORAGE}"; then
    echo "pct create on '${STORAGE}' failed - enabling rootdir on 'local' and retrying there..." >&2
    remote "pvesm set local --content rootdir,images,vztmpl,iso,backup,snippets" || true
    STORAGE=local
    create_ct "${STORAGE}"
  fi
  remote "pct start ${VMID}"
fi

echo "Waiting for container network ..."
for i in $(seq 1 15); do remote "pct exec ${VMID} -- ping -c1 -W1 ${CT_GW}" >/dev/null 2>&1 && break; sleep 2; done

echo "Provisioning PostgreSQL + UF inside the container ..."
remote "pct exec ${VMID} -- bash -c 'export DEBIAN_FRONTEND=noninteractive; (command -v curl >/dev/null || (apt-get update -y && apt-get install -y curl)); curl -fsSL ${DEPLOY_URL} | bash'"

echo "Done. PostgreSQL should be at ${CT_IP}:5432 (db=demo, user=demo)."
echo "The web-app (webapp-berlin) writes rows here on the 'Save entry to database' button."
