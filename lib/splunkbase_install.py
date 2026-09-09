#!/usr/bin/env python3
# ===========================================================================
# splunkbase_install.py - download + install Splunkbase apps at boot using
# splunk.com credentials (no redistribution: nothing is committed to the repo).
#
#   splunkbase_install.py <username> <password> <apps_dir> <app_id> [app_id ...]
#
# Flow (per Splunkbase's documented API):
#   1. POST /api/account:login/          -> auth token (<id> in the reply)
#   2. GET  /api/v2/apps/<id>/releases/  -> newest release version
#   3. GET  /api/v2/apps/<id>/releases/<ver>/download/?origin=sb -> 302 -> .tgz
#   4. extract the .tgz into <apps_dir>
#
# TLS verification stays ON for the login (it carries your real creds). Prints a
# status line per app; exits non-zero only if NOTHING installed.
# ===========================================================================
import io
import json
import re
import ssl
import sys
import tarfile
from urllib import request, parse, error

LOGIN = "https://splunkbase.splunk.com/api/account:login/"
REL = "https://api.splunkbase.splunk.com/api/v2/apps/%s/releases/"
DL = "https://api.splunkbase.splunk.com/api/v2/apps/%s/releases/%s/download/?origin=sb"

_ctx = ssl.create_default_context()  # verify ON - creds are posted here


def login(user, pw):
    data = parse.urlencode({"username": user, "password": pw}).encode()
    with request.urlopen(request.Request(LOGIN, data=data), timeout=30, context=_ctx) as r:
        body = r.read().decode("utf-8", "replace")
    m = re.search(r"<id>([^<]+)</id>", body)
    if not m:
        raise RuntimeError("login failed (check splunk.com username/password)")
    return m.group(1).strip()


def latest_version(app_id, token):
    req = request.Request(REL % app_id, headers={"X-Auth-Token": token, "Accept": "application/json"})
    with request.urlopen(req, timeout=30, context=_ctx) as r:
        d = json.loads(r.read().decode("utf-8"))
    rels = d.get("results") or d.get("releases") or (d if isinstance(d, list) else [])
    if not rels:
        raise RuntimeError("no releases returned")
    # newest first: sort by 'published'/'release_date' when present, else keep order
    rels = sorted(rels, key=lambda x: str(x.get("published") or x.get("release_date") or ""), reverse=True)
    ver = rels[0].get("name") or rels[0].get("title") or rels[0].get("version")
    if not ver:
        raise RuntimeError("could not determine latest version")
    return ver


def download(app_id, ver, token):
    req = request.Request(DL % (app_id, ver), headers={"X-Auth-Token": token})
    with request.urlopen(req, timeout=180, context=_ctx) as r:  # follows the 302
        return r.read()


def install(blob, apps_dir):
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        top = tar.getnames()[0].split("/")[0] if tar.getnames() else "?"
        tar.extractall(apps_dir)
    return top


def main():
    if len(sys.argv) < 5:
        sys.stderr.write("usage: splunkbase_install.py <user> <pass> <apps_dir> <app_id>...\n")
        sys.exit(2)
    user, pw, apps_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    app_ids = sys.argv[4:]
    try:
        token = login(user, pw)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("Splunkbase login failed: %s\n" % exc)
        sys.exit(1)

    ok = 0
    for app_id in app_ids:
        try:
            ver = latest_version(app_id, token)
            folder = install(download(app_id, ver, token), apps_dir)
            print("  installed app %s v%s -> %s" % (app_id, ver, folder))
            ok += 1
        except error.HTTPError as exc:
            sys.stderr.write("  app %s: HTTP %s (entitlement or version?) \n" % (app_id, exc.code))
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write("  app %s: %s\n" % (app_id, exc))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
