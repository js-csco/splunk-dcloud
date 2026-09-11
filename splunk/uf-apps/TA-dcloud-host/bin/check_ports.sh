#!/usr/bin/env bash
# ===========================================================================
# check_ports.sh - TCP reachability probe for services. One key=value line per
# target -> index=berlin_web sourcetype=port:probe. Pure bash (/dev/tcp), so it
# runs on a Universal Forwarder.
#   [script://./bin/check_ports.sh 198.18.3.50:8080 198.18.3.51:5432]
# Fields: target, host_addr, port, open (1/0), service.
# ===========================================================================
set -u
ts="$(date -u +%FT%TZ)"
name_for() { case "$1" in 8080) echo webapp;; 5432) echo postgres;; 8006) echo proxmox;; *) echo "port_$1";; esac; }
for t in "$@"; do
  host="${t%%:*}"; port="${t##*:}"
  open=0
  if timeout 3 bash -c ">/dev/tcp/${host}/${port}" 2>/dev/null; then open=1; fi
  printf 'metric_ts=%s source=port_probe site=berlin service=%s target=%s host_addr=%s port=%s open=%s\n' \
    "$ts" "$(name_for "$port")" "$t" "$host" "$port" "$open"
done
