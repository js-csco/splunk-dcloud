#!/usr/bin/env bash
# ===========================================================================
# enable-proxmox-snmpd.sh - install + configure the SNMP agent (snmpd) so
# Splunk Connect for SNMP (SC4SNMP) can POLL this host over SNMP (UDP 161).
# Run on the Proxmox host (root) - and optionally inside the LXC containers.
#
#   curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/snmp/enable-proxmox-snmpd.sh | bash
#
# Read-only community defaults to 'dcloud' (override with SNMP_COMMUNITY).
# ===========================================================================
set -euo pipefail
COMMUNITY="${SNMP_COMMUNITY:-dcloud}"
run_root() { if [ "$(id -u)" = "0" ]; then "$@"; else sudo "$@"; fi; }

export DEBIAN_FRONTEND=noninteractive
command -v snmpd >/dev/null 2>&1 || { run_root apt-get update -y && run_root apt-get install -y snmpd; }

run_root tee /etc/snmp/snmpd.conf >/dev/null <<CONF
# dcloud lab - read-only SNMP agent for SC4SNMP polling
agentAddress udp:161
rocommunity ${COMMUNITY}
sysLocation Berlin - dCloud lab
sysContact dcloud-lab
CONF

run_root systemctl enable snmpd >/dev/null 2>&1 || true
run_root systemctl restart snmpd
echo "snmpd ready on $(hostname): community='${COMMUNITY}', UDP/161. SC4SNMP can now poll this host."
