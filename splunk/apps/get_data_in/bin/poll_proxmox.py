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
KEYS = ("PROXMOX_HOST", "PROXMOX_PORT", "PROXMOX_USER", "PROXMOX_PASSWORD",
        "PROXMOX_TOKEN", "PROXMOX_SITE")


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


def emit_kv(obj):
    """Emit one key=value line (for metric events parsed on the indexer). String
    values are sanitized so every value is a single whitespace-free token."""
    parts = []
    for k, v in obj.items():
        if isinstance(v, str):
            v = v.replace(" ", "_").replace("=", "-") or "-"
        parts.append("%s=%s" % (k, v))
    sys.stdout.write(" ".join(parts) + "\n")


def get_ticket(base, user, password, ctx):
    data = parse.urlencode({"username": user, "password": password}).encode("utf-8")
    req = request.Request(base + "/access/ticket", data=data, method="POST")
    with request.urlopen(req, timeout=15, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))["data"]["ticket"]


def resolve_cfg():
    """Load config (file -> file -> env) and return the connection parameters."""
    cfg = {}
    load_into(APP_CFG, cfg)
    load_into(VAR_CFG, cfg)
    for k in KEYS:
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    host = cfg.get("PROXMOX_HOST")
    port = cfg.get("PROXMOX_PORT") or "8006"
    base = "https://%s:%s/api2/json" % (host, port) if host else None
    return {
        "host": host, "port": port, "base": base,
        "token": cfg.get("PROXMOX_TOKEN"),
        "user": cfg.get("PROXMOX_USER"),
        "password": cfg.get("PROXMOX_PASSWORD"),
        "site": cfg.get("PROXMOX_SITE") or "berlin",
    }


def build_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def auth_headers(c, ctx, now, emit_error):
    """Return request headers, or None (after emitting an error event)."""
    if c["token"]:
        return {"Authorization": "PVEAPIToken=%s" % c["token"]}
    try:
        return {"Cookie": "PVEAuthCookie=%s" % get_ticket(c["base"], c["user"], c["password"], ctx)}
    except Exception as exc:  # noqa: BLE001
        emit_error(exc)
        return None


def api_get(base, ep, headers, ctx):
    req = request.Request(base + "/" + ep, headers=headers)
    with request.urlopen(req, timeout=15, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8")).get("data")


def _num(v, default=0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def main():
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    c = resolve_cfg()
    ctx = build_ctx()

    if not c["host"] or not (c["token"] or (c["user"] and c["password"])):
        emit({"poll_time": now, "endpoint": "config", "status": "not_configured",
              "message": "No Proxmox host/creds. Set PROXMOX_HOST + PROXMOX_TOKEN or "
                         "PROXMOX_USER/PROXMOX_PASSWORD (see Get Data In - REST dashboard)."})
        return

    headers = auth_headers(
        c, ctx, now,
        lambda exc: emit({"poll_time": now, "endpoint": "access/ticket",
                          "status": "error", "message": "login failed: %s" % exc}))
    if headers is None:
        return

    for ep in ENDPOINTS:
        try:
            data = api_get(c["base"], ep, headers, ctx)
        except Exception as exc:  # noqa: BLE001
            emit({"poll_time": now, "endpoint": ep, "status": "error", "message": str(exc)})
            continue

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


def main_metrics():
    """Emit one JSON metric object per node/guest (sourcetype=proxmox:metrics ->
    a metric index). Numeric fields become measures; strings become dimensions."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    c = resolve_cfg()
    ctx = build_ctx()

    if not c["host"] or not (c["token"] or (c["user"] and c["password"])):
        return  # nothing to emit; the JSON poller already surfaces config errors

    headers = auth_headers(c, ctx, now, lambda exc: None)
    if headers is None:
        return
    try:
        rows = api_get(c["base"], "cluster/resources", headers, ctx) or []
    except Exception:  # noqa: BLE001
        return

    for r in rows:
        if not isinstance(r, dict):
            continue
        typ = r.get("type")
        maxmem = _num(r.get("maxmem"))
        mem = _num(r.get("mem"))
        maxdisk = _num(r.get("maxdisk"))
        disk = _num(r.get("disk"))
        m = {
            "metric_ts": now,
            "site": c["site"],
            "source": "proxmox",
            "cpu_pct": round(_num(r.get("cpu")) * 100, 2),
            "mem_used_pct": round(mem / maxmem * 100, 2) if maxmem else 0,
            "mem_used_mb": round(mem / 1048576, 1),
            "mem_total_mb": round(maxmem / 1048576, 1),
            "disk_used_pct": round(disk / maxdisk * 100, 2) if maxdisk else 0,
            "disk_gb": round(maxdisk / 1073741824, 2),
            "uptime_s": int(_num(r.get("uptime"))),
        }
        if typ == "node":
            m["otype"] = "node"
            m["oname"] = r.get("node", "")
            m["running"] = 1 if r.get("status") == "online" else 0
        elif typ in ("qemu", "lxc"):
            m["otype"] = "guest"
            m["oname"] = r.get("name", "")
            m["vmid"] = str(r.get("vmid", ""))
            m["gtype"] = typ
            m["node"] = r.get("node", "")
            m["running"] = 1 if r.get("status") == "running" else 0
        else:
            continue
        emit_kv(m)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "metrics":
        main_metrics()
    else:
        main()
