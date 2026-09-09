#!/usr/bin/env bash
# ===========================================================================
# create-webapp-container.sh - create the web-app LXC on the Berlin Proxmox and
# provision it. Run ON ubuntu-berlin (it can reach Proxmox in-location).
#
#   curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/create-webapp-container.sh | sudo bash
#
# It SSHes to Proxmox (root/cisco) and uses `pct` to create + start an
# unprivileged Ubuntu LXC at a fixed IP, then runs webapp/deploy-webapp.sh
# INSIDE the container (web-app + in-container UF).
#
# Overridable via env: PROXMOX_HOST, PROXMOX_PW, CT_VMID, CT_IP, CT_GW,
# CT_BRIDGE, CT_STORAGE, CT_TEMPLATE. Auto-detects gateway/storage/template when
# not given. Idempotent: if the VMID already exists it is (re)started + re-provisioned.
# ===========================================================================
set -euo pipefail

PROX="${PROXMOX_HOST:-198.18.3.17}"
PPW="${PROXMOX_PW:-cisco}"
VMID="${CT_VMID:-200}"
CTNAME="${CT_HOSTNAME:-webapp-berlin}"
CT_IP="${CT_IP:-198.18.3.50}"
BRIDGE="${CT_BRIDGE:-vmbr0}"
CT_GW="${CT_GW:-$(ip route 2>/dev/null | awk '/^default/{print $3; exit}')}"
REPO="${DCLOUD_REPO:-js-csco/splunk-dcloud}"; BRANCH="${DCLOUD_BRANCH:-main}"
DEPLOY_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/webapp/deploy-webapp.sh"

command -v sshpass >/dev/null 2>&1 || { export DEBIAN_FRONTEND=noninteractive; sudo apt-get update -y && sudo apt-get install -y sshpass; }
SSH=(sshpass -p "${PPW}" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 "root@${PROX}")

echo "== create webapp LXC ${VMID} (${CTNAME}) on Proxmox ${PROX}, ip ${CT_IP}, gw ${CT_GW:-?} =="
[ -n "${CT_GW:-}" ] || { echo "ERROR: could not determine gateway; pass CT_GW=..." >&2; exit 1; }

# reachability first (this is the recurring lab question)
if ! "${SSH[@]}" true 2>/dev/null; then
  echo "ERROR: cannot SSH to Proxmox at ${PROX}. Check reachability/creds (root/${PPW})." >&2
  exit 1
fi

remote() { "${SSH[@]}" "$@"; }

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
  remote "pct create ${VMID} '${TEMPLATE}' --hostname ${CTNAME} --cores 1 --memory 512 --swap 256 \
    --net0 name=eth0,bridge=${BRIDGE},ip=${CT_IP}/24,gw=${CT_GW} \
    --storage ${STORAGE} --rootfs ${STORAGE}:4 --unprivileged 1 --features nesting=1 \
    --onboot 1 --password '${PPW}' --description 'dCloud demo web-app (Splunk correlation)'"
  remote "pct start ${VMID}"
fi

echo "Waiting for container network ..."
for i in $(seq 1 15); do remote "pct exec ${VMID} -- ping -c1 -W1 ${CT_GW}" >/dev/null 2>&1 && break; sleep 2; done

echo "Provisioning web-app + UF inside the container ..."
remote "pct exec ${VMID} -- bash -c 'export DEBIAN_FRONTEND=noninteractive; (command -v curl >/dev/null || (apt-get update -y && apt-get install -y curl)); curl -fsSL ${DEPLOY_URL} | bash'"

echo "Done. Web-app should be at http://${CT_IP}:${WEBAPP_PORT:-8080}/"
echo "Reachability probe (from ubuntu-berlin UF) targets ${CT_IP}:${WEBAPP_PORT:-8080}."
