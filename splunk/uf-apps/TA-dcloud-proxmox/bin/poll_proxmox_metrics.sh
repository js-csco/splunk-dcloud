#!/bin/sh
# Wrapper so the Universal Forwarder (no bundled Python) can run the poller in
# METRICS mode using the host's system python3. Emits proxmox:metrics JSON.
d="$(dirname "$0")"
exec python3 "$d/poll_proxmox.py" metrics
