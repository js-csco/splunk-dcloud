#!/usr/bin/env python3
# ===========================================================================
# splunkbase_install.py - download + install Splunkbase apps at boot using
# splunk.com credentials (no redistribution: nothing is committed to the repo).
#
#   splunkbase_install.py <username> <password> <apps_dir> <app_id[:version]> ...
#
# Uses the classic, proven Splunkbase endpoints (host splunkbase.splunk.com):
#   1. POST /api/account:login/            -> auth token (<id> in the reply)
#   2. GET  /api/v1/app/<id>/release/       -> newest release version (X-Auth-Token)
#   3. GET  /app/<id>/release/<ver>/download/ -> 302 -> .tgz (X-Auth-Token)
#   4. extract into <apps_dir>
#
# Pin a version explicitly with app_id:version (e.g. 7931:1.2.0) to skip step 2.
# Prints the URL + HTTP code for whichever step fails, so 403s are diagnosable
# (a 403 on download usually means the account hasn't accepted that app's terms
# once in the browser).
# ===========================================================================
import io
import re
import ssl
import sys
import tarfile
from urllib import request, parse, error

LOGIN = "https://splunkbase.splunk.com/api/account:login/"
RELEASES = "https://splunkbase.splunk.com/api/v1/app/%s/release/"
DOWNLOAD = "https://splunkbase.splunk.com/app/%s/release/%s/download/"

_ctx = ssl.create_default_context()  # verify ON - creds are posted here


def _get(url, token=None, timeout=180):
    headers = {"User-Agent": "dcloud-lab"}
    if token:
        headers["X-Auth-Token"] = token
    return request.urlopen(request.Request(url, headers=headers), timeout=timeout, context=_ctx)


def login(user, pw):
    data = parse.urlencode({"username": user, "password": pw}).encode()
    with request.urlopen(request.Request(LOGIN, data=data), timeout=30, context=_ctx) as r:
        body = r.read().decode("utf-8", "replace")
    m = re.search(r"<id>([^<]+)</id>", body)
    if not m:
        raise RuntimeError("login failed (check splunk.com username/password)")
    return m.group(1).strip()


def latest_version(app_id, token):
    with _get(RELEASES % app_id, token, timeout=30) as r:
        body = r.read().decode("utf-8", "replace")
    # Atom XML: entries carry .../release/<version>/ in their <id>; newest first.
    vers = re.findall(r"/release/([^/<>\"]+)/", body)
    if not vers:
        vers = re.findall(r"<title>([^<]+)</title>", body)[1:]  # skip feed title
    if not vers:
        raise RuntimeError("could not determine latest version from release list")
    return vers[0]


def install(blob, apps_dir):
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        names = tar.getnames()
        top = names[0].split("/")[0] if names else "?"
        tar.extractall(apps_dir)
    return top


def do_app(spec, token, apps_dir):
    app_id, _, ver = spec.partition(":")
    step, url = "releases", RELEASES % app_id
    if not ver:
        ver = latest_version(app_id, token)
    step, url = "download", DOWNLOAD % (app_id, ver)
    with _get(url, token) as r:
        blob = r.read()
    folder = install(blob, apps_dir)
    print("  installed app %s v%s -> %s" % (app_id, ver, folder))


def main():
    if len(sys.argv) < 5:
        sys.stderr.write("usage: splunkbase_install.py <user> <pass> <apps_dir> <app_id[:version]>...\n")
        sys.exit(2)
    user, pw, apps_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    specs = sys.argv[4:]
    try:
        token = login(user, pw)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("Splunkbase login failed: %s\n" % exc)
        sys.exit(1)

    ok = 0
    for spec in specs:
        try:
            do_app(spec, token, apps_dir)
            ok += 1
        except error.HTTPError as exc:
            sys.stderr.write("  app %s: HTTP %s at %s\n" % (spec, exc.code, exc.url))
            if exc.code == 403:
                sys.stderr.write("    -> 403 usually means the account must accept this app's "
                                 "terms once at splunkbase.splunk.com/app/%s in a browser.\n"
                                 % spec.split(':')[0])
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write("  app %s: %s\n" % (spec, exc))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
