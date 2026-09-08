#!/usr/bin/env python3
# ===========================================================================
# proxmox-guest.py - start/stop Proxmox VMs & containers via the API, so you
# can change state and watch it appear in Splunk within ~60s (next UF poll).
#
# Fully automatic: logs in with username/password (ticket auth) and uses the
# CSRF token from that same login for the write call - no API token needed.
#
# Run from any box that can reach Proxmox (ubuntu-berlin or the Splunk host):
#   curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/proxmox-guest.py | python3 - list
#   curl -fsSL <url> | python3 - start 101
#   curl -fsSL <url> | python3 - stop  web-01
#   curl -fsSL <url> | python3 - reboot 101
#
# Override the (baked-in lab) target via env: PROXMOX_HOST/USER/PASSWORD/PORT.
# ===========================================================================
import json
import os
import ssl
import subprocess
import sys
from urllib import request, parse

HOST = os.environ.get("PROXMOX_HOST", "198.18.3.170")
PORT = os.environ.get("PROXMOX_PORT", "8006")
USER = os.environ.get("PROXMOX_USER", "root@pam")
PASSWORD = os.environ.get("PROXMOX_PASSWORD", "cisco")
BASE = "https://%s:%s/api2/json" % (HOST, PORT)
ACTIONS = ("start", "stop", "shutdown", "reboot", "suspend", "resume")

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def die(msg, code=1):
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def login():
    data = parse.urlencode({"username": USER, "password": PASSWORD}).encode()
    with request.urlopen(request.Request(BASE + "/access/ticket", data=data), timeout=15, context=_ctx) as r:
        d = json.loads(r.read().decode())["data"]
    return d["ticket"], d["CSRFPreventionToken"]


def api_get(path, ticket):
    req = request.Request(BASE + path, headers={"Cookie": "PVEAuthCookie=" + ticket})
    with request.urlopen(req, timeout=15, context=_ctx) as r:
        return json.loads(r.read().decode()).get("data")


def api_post(path, ticket, csrf):
    req = request.Request(BASE + path, data=b"", method="POST",
                          headers={"Cookie": "PVEAuthCookie=" + ticket, "CSRFPreventionToken": csrf})
    with request.urlopen(req, timeout=30, context=_ctx) as r:
        return json.loads(r.read().decode()).get("data")


def guests(ticket):
    out = []
    for it in api_get("/cluster/resources?type=vm", ticket) or []:
        if it.get("type") in ("qemu", "lxc"):
            out.append(it)
    return out


def find(ticket, ident):
    for g in guests(ticket):
        if str(g.get("vmid")) == str(ident) or g.get("name") == ident:
            return g
    return None


def cmd_list(ticket):
    rows = guests(ticket)
    print("%-6s %-6s %-16s %-8s %s" % ("VMID", "TYPE", "NAME", "STATUS", "NODE"))
    for g in sorted(rows, key=lambda x: x.get("vmid", 0)):
        print("%-6s %-6s %-16s %-8s %s" % (g.get("vmid"), g.get("type"),
              g.get("name", ""), g.get("status", ""), g.get("node", "")))
    if not rows:
        print("(no VMs/containers found)")


def cmd_action(action, ident):
    ticket, csrf = login()
    g = find(ticket, ident)
    if not g:
        die("No VM/container matching '%s'. Try: list" % ident)
    node, typ, vmid = g["node"], g["type"], g["vmid"]
    path = "/nodes/%s/%s/%s/status/%s" % (node, typ, vmid, action)
    task = api_post(path, ticket, csrf)
    print("OK: %s %s/%s (%s) on %s -> task %s" % (action, typ, vmid, g.get("name", ""), node, task))
    # Best-effort audit event into syslog (picked up by the local forwarder).
    try:
        subprocess.run(["logger", "-t", "proxmox-ctl",
                        "action=%s type=%s vmid=%s name=%s node=%s" %
                        (action, typ, vmid, g.get("name", ""), node)], check=False)
    except Exception:  # noqa: BLE001
        pass


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        die("usage: proxmox-guest.py list | <%s> <vmid|name>" % "|".join(ACTIONS), 0)
    if args[0] == "list":
        t, _ = login()
        cmd_list(t)
        return
    if args[0] in ACTIONS:
        if len(args) < 2:
            die("usage: proxmox-guest.py %s <vmid|name>" % args[0])
        cmd_action(args[0], args[1])
        return
    die("unknown command '%s' (use: list | %s)" % (args[0], "|".join(ACTIONS)))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        die("ERROR: %s" % exc)
