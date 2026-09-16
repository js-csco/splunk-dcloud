#!/usr/bin/env bash
# ===========================================================================
# set-static-ip.sh <ip/cidr> [gateway] [iface] - pin an Ubuntu host to a STATIC
# address so it stops pulling a DHCP lease that collides with another lab host.
#
# Why: the dCloud Ubuntu VMs default to DHCP. In Berlin that let ubuntu-berlin
# grab 198.18.3.52 - the address reserved for the SC4SNMP collector - so the two
# boxes fought over one IP and the ITSI probes / SSH flapped. Give each Ubuntu a
# fixed address OUTSIDE the churn and the collision is gone.
#
# Recommended lab addressing (matches the topology table):
#   ubuntu-berlin        198.18.3.22/24
#   ubuntu-berlin-snmp   198.18.3.52/24   (the SC4SNMP collector)
#   ubuntu-london        198.18.2.22/24
#
# Run ON the box, from the CONSOLE (not SSH): applying a new address drops any
# SSH session on the OLD address. Example on ubuntu-berlin:
#   curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/set-static-ip.sh | sudo bash -s -- 198.18.3.22/24
#
# The lab wipes VMs each rebuild, so re-run this each session (or once via the
# console) - it's idempotent.
# ===========================================================================
set -euo pipefail

CIDR="${1:?usage: set-static-ip.sh <ip/cidr> [gateway] [iface]  e.g. 198.18.3.22/24}"
IP="${CIDR%/*}"
run_root() { if [ "$(id -u)" = "0" ]; then "$@"; else sudo "$@"; fi; }

# Interface: arg 3, else the one carrying the current default route.
IFACE="${3:-$(ip route show default 2>/dev/null | awk '{print $5; exit}')}"
[ -n "${IFACE}" ] || IFACE="$(ip -o link show 2>/dev/null | awk -F': ' '$2!="lo"{print $2; exit}')"
[ -n "${IFACE}" ] || { echo "ERROR: could not detect a network interface - pass it as arg 3." >&2; exit 1; }

# Gateway: arg 2, else .1 of this /24.
GW="${2:-$(echo "${IP}" | awk -F. '{print $1"."$2"."$3".1"}')}"
DNS="${DNS:-${GW}}"

echo "== pinning ${IFACE} -> ${CIDR} (gw ${GW}, dns ${DNS}) =="

if ! command -v netplan >/dev/null 2>&1; then
  echo "ERROR: netplan not found. This host isn't using netplan; tell me its network stack." >&2
  exit 1
fi

# Stop cloud-init from re-writing DHCP config on the next boot.
run_root mkdir -p /etc/cloud/cloud.cfg.d
echo 'network: {config: disabled}' | run_root tee /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg >/dev/null

# Neutralise any DHCP netplan cloud-init already dropped in (e.g. 50-cloud-init.yaml),
# so our file isn't merged with a dhcp4:true stanza for the same interface.
for f in /etc/netplan/50-cloud-init.yaml /etc/netplan/00-installer-config.yaml; do
  [ -f "$f" ] && run_root mv "$f" "${f}.dcloud-bak" 2>/dev/null || true
done

F=/etc/netplan/99-dcloud-static.yaml
run_root tee "$F" >/dev/null <<YAML
network:
  version: 2
  renderer: networkd
  ethernets:
    ${IFACE}:
      dhcp4: false
      dhcp6: false
      addresses: [${CIDR}]
      routes:
        - to: default
          via: ${GW}
      nameservers:
        addresses: [${DNS}]
YAML
run_root chmod 600 "$F"

echo "Wrote ${F}. Applying now - if you are on SSH via the OLD address, this will DROP;"
echo "reconnect to ${IP}."
run_root netplan apply
echo "Done. ${IFACE} is now ${CIDR}. Verify:  ip -4 addr show ${IFACE}"
