#!/usr/bin/env bash
# ===========================================================================
# enable-snmpd.sh - install + configure the SNMP agent (snmpd) on ANY Debian/
# Ubuntu host so SC4SNMP can POLL it over SNMP (UDP 161). Run on each Linux
# device you want in SNMP: the Proxmox host, ubuntu-desktop-london, etc.
#
#   curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/snmp/enable-snmpd.sh | sudo bash
#
# Read-only community defaults to 'dcloud' (override SNMP_COMMUNITY). Set
# SNMP_LOCATION to label where the host lives. Also points snmpd's own trap
# sink at SC4SNMP (198.18.3.52) so agent traps (e.g. restarts) are captured.
# ===========================================================================
set -euo pipefail
COMMUNITY="${SNMP_COMMUNITY:-dcloud}"
LOCATION="${SNMP_LOCATION:-dCloud lab}"
TRAP_SINK="${SNMP_TRAP_TARGET:-198.18.3.52}"
run_root() { if [ "$(id -u)" = "0" ]; then "$@"; else sudo "$@"; fi; }

export DEBIAN_FRONTEND=noninteractive
command -v snmpd >/dev/null 2>&1 || { run_root apt-get update -y && run_root apt-get install -y snmpd; }

# rocommunity with no default agentAddress restriction -> listen on all
# interfaces UDP/161 and expose the full standard tree (system, IF-MIB,
# HOST-RESOURCES-MIB, UCD-SNMP-MIB: CPU/mem/disk/load).
run_root tee /etc/snmp/snmpd.conf >/dev/null <<CONF
# dcloud lab - read-only SNMP agent for SC4SNMP polling
agentAddress udp:161
rocommunity ${COMMUNITY}
sysLocation ${LOCATION}
sysContact dcloud-lab
# emit an snmpd start/stop trap to SC4SNMP
trap2sink ${TRAP_SINK}:162 ${COMMUNITY}
CONF

run_root systemctl enable snmpd >/dev/null 2>&1 || true
run_root systemctl restart snmpd
echo "snmpd ready on $(hostname): community='${COMMUNITY}', UDP/161, traps -> ${TRAP_SINK}:162."
echo "Add this host's IP to the SC4SNMP inventory (snmp/setup-sc4snmp.sh) to poll it."
