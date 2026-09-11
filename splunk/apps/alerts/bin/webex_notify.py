#!/usr/bin/env python3
# ===========================================================================
# webex_notify.py - custom Splunk alert action: send a message to Webex.
#
# Splunk runs this with `--execute` and a JSON payload on stdin (because
# alert_actions.conf sets payload_format = json). We read the message from the
# alert's configuration and the Webex credential from an OUT-OF-REPO file
# ($SPLUNK_HOME/var/lib/dcloud/webex.env), so no secret is committed.
#
# webex.env supports either:
#   WEBEX_WEBHOOK_URL=https://webexapis.com/v1/webhooks/incoming/XXXX   (simplest)
#   -- or a bot --
#   WEBEX_BOT_TOKEN=...            and   WEBEX_ROOM_ID=...
#
# apply.sh prompts for the webhook URL (or token+room) and writes webex.env.
# ===========================================================================
import json
import os
import sys
import ssl
from urllib import request, error

VAR_ENV = os.path.join(os.environ.get("SPLUNK_HOME", "/opt/splunk"), "var", "lib", "dcloud", "webex.env")
MESSAGES_API = "https://webexapis.com/v1/messages"
_ctx = ssl.create_default_context()


def load_env():
    cfg = {}
    for path in (VAR_ENV,):
        try:
            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        cfg[k.strip()] = v.strip()
        except FileNotFoundError:
            pass
    for k in ("WEBEX_WEBHOOK_URL", "WEBEX_BOT_TOKEN", "WEBEX_ROOM_ID"):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


def post(url, data, headers):
    body = json.dumps(data).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers)
    req = request.Request(url, data=body, headers=hdrs, method="POST")
    with request.urlopen(req, timeout=15, context=_ctx) as r:
        return r.status


def main():
    if len(sys.argv) < 2 or sys.argv[1] != "--execute":
        sys.stderr.write("webex_notify: expected --execute\n")
        return 2
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("webex_notify: bad payload: %s\n" % exc)
        return 2

    cfg = payload.get("configuration", {}) or {}
    name = payload.get("search_name", "Splunk alert")
    link = payload.get("results_link", "")
    msg = cfg.get("message") or ("Splunk alert fired: %s" % name)
    if link:
        msg = "%s\n\n[View in Splunk](%s)" % (msg, link)

    env = load_env()
    try:
        if env.get("WEBEX_WEBHOOK_URL"):
            code = post(env["WEBEX_WEBHOOK_URL"], {"markdown": msg}, {})
        elif env.get("WEBEX_BOT_TOKEN") and env.get("WEBEX_ROOM_ID"):
            code = post(MESSAGES_API, {"roomId": env["WEBEX_ROOM_ID"], "markdown": msg},
                        {"Authorization": "Bearer %s" % env["WEBEX_BOT_TOKEN"]})
        else:
            sys.stderr.write("webex_notify: no Webex credential in %s "
                             "(set WEBEX_WEBHOOK_URL or WEBEX_BOT_TOKEN+WEBEX_ROOM_ID).\n" % VAR_ENV)
            return 1
        sys.stderr.write("webex_notify: sent (HTTP %s)\n" % code)
        return 0
    except error.HTTPError as exc:
        sys.stderr.write("webex_notify: HTTP %s from Webex: %s\n" % (exc.code, exc.read()[:200]))
        return 1
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("webex_notify: send failed: %s\n" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
