#!/usr/bin/env python3
# ===========================================================================
# poll_proxmox.py - Splunk scripted input: poll the Proxmox REST API.
#
# Runs on the Splunk host every N seconds (see inputs.conf), fetches a few
# Proxmox API endpoints, and prints one JSON event per resource to stdout,
# which Splunk indexes (sourcetype=proxmox:api -> index berlin_proxmox).
#
# Config is read from (later overrides earlier):
#   1. <app>/bin/proxmox_config.env   (committed lab defaults)
#   2. $SPLUNK_HOME/var/lib/dcloud/proxmox.env   (runtime override / secrets)
#   3. environment variables
# Keys: PROXMOX_HOST, PROXMOX_PORT, and either PROXMOX_TOKEN (API token) or
#       PROXMOX_USER + PROXMOX_PASSWORD (ticket auth).
#
# Scripted inputs run in splunkd's context (not the search sandbox), so the
# outbound HTTPS works. Self-guards if nothing is configured.
# ===========================================================================
import json
import os
import ssl
import sys
import time
from urllib import request, parse

SPLUNK_HOME = os.environ.get("SPLUNK_HOME", "/opt/splunk")
APP_CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxmox_config.env")
VAR_CFG = os.path.join(SPLUNK_HOME, "var", "lib", "dcloud", "proxmox.env")
ENDPOINTS = ["version", "cluster/resources", "nodes"]
KEYS = ("PROXMOX_HOST", "PROXMOX_PORT", "PROXMOX_USER", "PROXMOX_PASSWORD", "PROXMOX_TOKEN")


def load_into(path, cfg):
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


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")


def get_ticket(base, user, password, ctx):
    data = parse.urlencode({"username": user, "password": password}).encode("utf-8")
    req = request.Request(base + "/access/ticket", data=data, method="POST")
    with request.urlopen(req, timeout=15, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))["data"]["ticket"]


def main():
    cfg = {}
    load_into(APP_CFG, cfg)
    load_into(VAR_CFG, cfg)
    for k in KEYS:
        if os.environ.get(k):
            cfg[k] = os.environ[k]

    host = cfg.get("PROXMOX_HOST")
    port = cfg.get("PROXMOX_PORT") or "8006"
    token = cfg.get("PROXMOX_TOKEN")
    user = cfg.get("PROXMOX_USER")
    password = cfg.get("PROXMOX_PASSWORD")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    base = "https://%s:%s/api2/json" % (host, port) if host else None

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    if not host or not (token or (user and password)):
        emit({"poll_time": now, "endpoint": "config", "status": "not_configured",
              "message": "No Proxmox host/creds. Set PROXMOX_HOST + PROXMOX_TOKEN or "
                         "PROXMOX_USER/PROXMOX_PASSWORD (see Get Data In - REST dashboard)."})
        return

    headers = {}
    if token:
        headers["Authorization"] = "PVEAPIToken=%s" % token
    else:
        try:
            headers["Cookie"] = "PVEAuthCookie=%s" % get_ticket(base, user, password, ctx)
        except Exception as exc:  # noqa: BLE001
            emit({"poll_time": now, "endpoint": "access/ticket", "status": "error",
                  "message": "login failed: %s" % exc})
            return

    for ep in ENDPOINTS:
        req = request.Request(base + "/" + ep, headers=headers)
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
