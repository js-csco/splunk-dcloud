#!/usr/bin/env bash
# ===========================================================================
# ssh_router.sh - Splunk scripted input: pull "show" data from a Cisco device.
#
# Called by inputs.conf as:  [script://./bin/ssh_router.sh <site>]
# Looks up the device for <site> in routers.csv (next to this script), SSHes in
# with sshpass, runs a set of show commands, and prints one event per command.
#
# Scripted inputs run in splunkd's context (not the search sandbox), so the
# outbound SSH works. Each event starts with a "##DCLOUD##" marker + key="val"
# header so props.conf (sourcetype=cisco:ios) can break/parse them.
# ===========================================================================
set -uo pipefail

SITE="${1:-}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CSV="${DIR}/routers.csv"
ts="$(date -u +%FT%TZ)"

note() { printf '##DCLOUD## ts="%s" site="%s" status="%s" message="%s"\n' "$ts" "$SITE" "$1" "$2"; }

[ -n "$SITE" ] || { note error "no site argument"; exit 0; }
[ -f "$CSV" ]  || { note error "routers.csv not found"; exit 0; }
command -v sshpass >/dev/null 2>&1 || { note error "sshpass not installed on the Splunk host"; exit 0; }

# Find the row for this site: columns = site,name,host,username,password
row="$(awk -F, -v s="$SITE" '$1==s {print; exit}' "$CSV")"
[ -n "$row" ] || { note error "no router for site '$SITE' in routers.csv"; exit 0; }
IFS=',' read -r r_site r_name r_host r_user r_pass <<< "$row"

CMDS=(
  "show version"
  "show inventory"
  "show license summary"
  "show ntp status"
  "show ntp associations"
  "show crypto pki certificates"
  "show ip interface brief"
  "show clock"
)

# SSH options: no host-key prompts, short timeout, and enable legacy KEX/host-key
# algorithms in case the IOS device only offers older ones.
SSHOPTS=(
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o ConnectTimeout=12
  -o LogLevel=ERROR
  -o KexAlgorithms=+diffie-hellman-group14-sha1,diffie-hellman-group-exchange-sha1,diffie-hellman-group1-sha1
  -o HostKeyAlgorithms=+ssh-rsa,ssh-dss
  -o PubkeyAcceptedAlgorithms=+ssh-rsa
  -o Ciphers=+aes128-cbc,aes256-cbc,3des-cbc
)

for cmd in "${CMDS[@]}"; do
  out="$(sshpass -p "$r_pass" ssh "${SSHOPTS[@]}" "${r_user}@${r_host}" "$cmd" 2>&1)"
  printf '##DCLOUD## ts="%s" site="%s" name="%s" host="%s" command="%s"\n%s\n' \
    "$ts" "$SITE" "$r_name" "$r_host" "$cmd" "$out"
done
