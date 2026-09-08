#!/usr/bin/env python3
# ===========================================================================
# poll_proxmox.py - Splunk scripted input: poll the Proxmox REST API.
#
# Runs on the Splunk host every N seconds (see inputs.conf), fetches a few
# Proxmox API endpoints, and prints one JSON event per resource to stdout,
# which Splunk indexes (sourcetype=proxmox:api -> index berlin_proxmox).
#
# Scripted inputs run in splunkd's context (NOT the search sandbox), so the
# outbound HTTPS call works. Credentials are read from a file written by
# apply.sh at boot: $SPLUNK_HOME/var/lib/dcloud/proxmox.env
#   PROXMOX_HOST=198.18.3.x
#   PROXMOX_TOKEN=user@pam!tokenid=xxxxxxxx-....
#   PROXMOX_PORT=8006            (optional; defaults to 8006)
#
# If no creds are present it emits one explanatory event and exits cleanly, so
# the input never hard-fails.
# ===========================================================================
import json
import os
import ssl
import sys
import time
from urllib import request

SPLUNK_HOME = os.environ.get("SPLUNK_HOME", "/opt/splunk")
ENV_FILE = os.path.join(SPLUNK_HOME, "var", "lib", "dcloud", "proxmox.env")
ENDPOINTS = ["version", "cluster/resources", "nodes"]


def load_env(path):
    cfg = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return cfg


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")


def main():
    cfg = load_env(ENV_FILE)
    host = cfg.get("PROXMOX_HOST") or os.environ.get("PROXMOX_HOST")
    token = cfg.get("PROXMOX_TOKEN") or os.environ.get("PROXMOX_TOKEN")
    port = cfg.get("PROXMOX_PORT") or os.environ.get("PROXMOX_PORT") or "8006"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if not host or not token:
        emit({"poll_time": now, "endpoint": "config", "status": "not_configured",
              "message": "No Proxmox creds. Set PROXMOX_HOST/PROXMOX_TOKEN (see README - Get Data In)."})
        return

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for ep in ENDPOINTS:
        url = "https://%s:%s/api2/json/%s" % (host, port, ep)
        req = request.Request(url, headers={"Authorization": "PVEAPIToken=%s" % token})
        try:
            with request.urlopen(req, timeout=15, context=ctx) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            emit({"poll_time": now, "endpoint": ep, "status": "error", "message": str(exc)})
            continue

        data = payload.get("data")
        if isinstance(data, list):
            for item in data:
                row = dict(item) if isinstance(item, dict) else {"value": item}
                row.update({"poll_time": now, "endpoint": ep})
                emit(row)
        elif isinstance(data, dict):
            row = dict(data)
            row.update({"poll_time": now, "endpoint": ep})
            emit(row)
        else:
            emit({"poll_time": now, "endpoint": ep, "data": data})


if __name__ == "__main__":
    main()
