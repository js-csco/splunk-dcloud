#!/usr/bin/env python3
# ===========================================================================
# git_push_watcher.py - scripted input that drains the Save-to-GitHub queue.
#
# Runs every 30s in splunkd context (network works). Reads pending requests from
# the git_save_requests KV Store, runs labsync.sh <branch> <message> to push a
# new branch, and writes the result back to the KV Store so the dashboard can
# show live status. Prints one JSON line per processed request (indexed as an
# audit trail).
#
# Auth: passAuth=splunk-system-user delivers a session key on stdin; SPLUNKD_URI
# points at the local management port.
# ===========================================================================
import json
import os
import ssl
import subprocess
import sys
import time
from urllib import request, parse

SPLUNK_HOME = os.environ.get("SPLUNK_HOME", "/opt/splunk")
URI = os.environ.get("SPLUNKD_URI", "https://127.0.0.1:8089").rstrip("/")
APP = "dcloud_lab"
COLL = "git_save_requests"
LABSYNC = os.path.join(SPLUNK_HOME, "etc", "apps", "dcloud_lab", "bin", "labsync.sh")

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def now():
    return time.strftime("%F %T", time.gmtime())


def session_key():
    # passAuth passes the session key on stdin (one line).
    try:
        data = sys.stdin.read().strip()
    except Exception:  # noqa: BLE001
        data = ""
    if data:
        return data.splitlines()[-1].strip()
    return ""


def kv_url(key=None):
    base = "%s/servicesNS/nobody/%s/storage/collections/data/%s" % (URI, APP, COLL)
    return base + ("/" + parse.quote(str(key)) if key else "")


def api(url, key, method="GET", body=None):
    headers = {"Authorization": "Splunk %s" % key}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = request.Request(url, data=data, method=method, headers=headers)
    with request.urlopen(req, timeout=30, context=_ctx) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else None


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")


def main():
    key = session_key()
    if not key:
        emit({"event": "git_push_watcher", "status": "error", "message": "no session key (passAuth)"})
        return
    try:
        q = parse.quote(json.dumps({"status": "pending"}))
        pending = api(kv_url() + "?query=" + q, key)
    except Exception as exc:  # noqa: BLE001
        emit({"event": "git_push_watcher", "status": "error", "message": "kv query failed: %s" % exc})
        return

    for doc in (pending or []):
        k = doc.get("_key")
        branch = doc.get("branch") or "lab-snapshot"
        msg = doc.get("message") or "Lab snapshot from Splunk UI"

        doc["status"] = "running"; doc["updated_at"] = now()
        try:
            api(kv_url(k), key, "POST", doc)
        except Exception:  # noqa: BLE001
            pass

        try:
            out = subprocess.check_output(["/bin/bash", LABSYNC, branch, msg],
                                          stderr=subprocess.STDOUT, universal_newlines=True, timeout=180)
            lines = [ln for ln in out.strip().splitlines() if ln.strip()]
            res = json.loads(lines[-1]) if lines else {"status": "error", "message": "no output"}
        except subprocess.CalledProcessError as exc:
            res = {"status": "error", "message": (exc.output or "labsync failed")[-400:]}
        except Exception as exc:  # noqa: BLE001
            res = {"status": "error", "message": str(exc)[-400:]}

        st = res.get("status")
        doc["status"] = "done" if st == "ok" else ("nochange" if st == "nochange" else "error")
        doc["result"] = (res.get("message") or "")[:400]
        doc["commit"] = res.get("commit", "")
        doc["branch"] = res.get("branch", branch)
        doc["updated_at"] = now()
        try:
            api(kv_url(k), key, "POST", doc)
        except Exception:  # noqa: BLE001
            pass
        emit({"event": "git_push_watcher", "key": k, "branch": doc["branch"],
              "status": doc["status"], "commit": doc["commit"], "result": doc["result"]})


if __name__ == "__main__":
    main()
