#!/bin/sh
# Wrapper so the Universal Forwarder (which does not bundle Python) can run the
# poller using the host's system python3. Called by the UF scripted input.
d="$(dirname "$0")"
exec python3 "$d/poll_proxmox.py"
