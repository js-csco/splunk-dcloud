#!/usr/bin/env bash
# ===========================================================================
# create-webapp-container.sh - create the web-app LXC on the Berlin Proxmox and
# provision it. Run it EITHER:
#   * directly on the Proxmox host as root (recommended - runs pct locally, no
#     SSH; Proxmox is Debian and has NO sudo, so use plain `bash`, not sudo):
#       curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/create-webapp-container.sh | bash
#   * OR on ubuntu-berlin (SSHes to Proxmox; that box has sudo):
#       curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/create-webapp-container.sh | sudo bash
#
# It SSHes to Proxmox (root/C1sco12345) and uses `pct` to create + start an
# unprivileged Ubuntu LXC at a fixed IP, then runs webapp/deploy-webapp.sh
# INSIDE the container (web-app + in-container UF).
#
# Overridable via env: PROXMOX_HOST, PROXMOX_PW, CT_VMID, CT_IP, CT_GW,
# CT_BRIDGE, CT_STORAGE, CT_TEMPLATE. Auto-detects gateway/storage/template when
# not given. Idempotent: if the VMID already exists it is (re)started + re-provisioned.
# ===========================================================================
set -euo pipefail

PROX="${PROXMOX_HOST:-198.18.3.11}"
PPW="${PROXMOX_PW:-C1sco12345}"
VMID="${CT_VMID:-200}"
CTNAME="${CT_HOSTNAME:-webapp-berlin}"
CT_IP="${CT_IP:-198.18.3.50}"
BRIDGE="${CT_BRIDGE:-vmbr0}"
CT_GW="${CT_GW:-$(ip route 2>/dev/null | awk '/^default/{print $3; exit}')}"
REPO="${DCLOUD_REPO:-js-csco/splunk-dcloud}"; BRANCH="${DCLOUD_BRANCH:-main}"
DEPLOY_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/webapp/deploy-webapp.sh"

run_root() { if [ "$(id -u)" = "0" ]; then "$@"; else sudo "$@"; fi; }

# Works two ways:
#   * ON ubuntu-berlin  -> SSH into Proxmox (needs sshpass; installs it)
#   * ON the Proxmox host itself (root) -> run pct locally, no SSH/sudo needed
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

echo "== create webapp LXC ${VMID} (${CTNAME}) on Proxmox ${PROX}, ip ${CT_IP}, gw ${CT_GW:-?} =="
[ -n "${CT_GW:-}" ] || { echo "ERROR: could not determine gateway; pass CT_GW=..." >&2; exit 1; }

if remote "pct status ${VMID}" >/dev/null 2>&1; then
  echo "Container ${VMID} already exists - ensuring it is running."
  remote "pct start ${VMID} || true"
else
  # storage for the rootfs (first storage that supports container root)
  STORAGE="${CT_STORAGE:-$(remote "pvesm status -content rootdir 2>/dev/null | awk 'NR>1{print \$1; exit}'")}"
  [ -n "${STORAGE:-}" ] || STORAGE="local-lvm"
  # template: reuse a downloaded ubuntu one, else download the latest 22.04
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
    remote "pct create ${VMID} '${TEMPLATE}' --hostname ${CTNAME} --cores 1 --memory 512 --swap 256 \
      --net0 name=eth0,bridge=${BRIDGE},ip=${CT_IP}/24,gw=${CT_GW} \
      --storage $1 --rootfs $1:4 --unprivileged 1 --features nesting=1 \
      --onboot 1 --password '${PPW}' --description 'dCloud demo web-app (Splunk correlation)'"
  }
  # The detected storage (often local-lvm) can be defined but unusable on dCloud
  # Proxmox (e.g. "no such logical volume pve/data"). If create fails, fall back to
  # the directory storage 'local' - enable container content on it and retry.
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

echo "Provisioning web-app + UF inside the container ..."
remote "pct exec ${VMID} -- bash -c 'export DEBIAN_FRONTEND=noninteractive; (command -v curl >/dev/null || (apt-get update -y && apt-get install -y curl)); curl -fsSL ${DEPLOY_URL} | bash'"

echo "Done. Web-app should be at http://${CT_IP}:${WEBAPP_PORT:-8080}/"
echo "Reachability probe (from ubuntu-berlin UF) targets ${CT_IP}:${WEBAPP_PORT:-8080}."
