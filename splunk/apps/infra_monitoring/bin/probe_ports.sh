#!/usr/bin/env bash
# ===========================================================================
# probe_ports.sh <ip> <port> [node] - active TCP reachability probe, run FROM
# the Splunk server (a host that is always up) against a remote service.
#
# Why this exists: a container's own metrics heartbeat DISAPPEARS when the
# container is stopped - and "missing data" is a GAP, which ITSI paints slowly
# and amber, not decisively red. And if only the *app* crashes while the
# container stays up, the heartbeat keeps ticking green forever. This probe
# instead emits a PRESENT value every run - open=1 when the port answers,
# open=0 when it does not - so ITSI Reachability flips to RED the instant the
# service stops answering, and back to GREEN when it returns. It tests the
# actual service port, so "container up / service dead" is caught too.
#
# Pure bash (/dev/tcp), no extra packages. Wire it up in inputs.conf as a
# scripted input on the Splunk server, one stanza per target, e.g.:
#   [script://./bin/probe_ports.sh 198.18.3.51 5432 db-berlin]
#   index = berlin_web
#   sourcetype = port:probe
#   host = db-berlin
#   interval = 60
# ===========================================================================
set -u
IP="${1:?usage: probe_ports.sh <ip> <port> [node]}"
PORT="${2:?usage: probe_ports.sh <ip> <port> [node]}"
NODE="${3:-${IP}:${PORT}}"

ts="$(date -u +%FT%TZ)"
open=0
if timeout 3 bash -c ">/dev/tcp/${IP}/${PORT}" 2>/dev/null; then open=1; fi

printf 'metric_ts=%s source=port_probe node=%s target=%s:%s port=%s open=%s\n' \
  "$ts" "$NODE" "$IP" "$PORT" "$PORT" "$open"
