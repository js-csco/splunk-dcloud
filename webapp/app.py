#!/usr/bin/env python3
# ===========================================================================
# app.py - a tiny demo web-app for the "Client -> App -> Hypervisor" correlation
# story. Runs in an LXC container on Proxmox (Berlin). Pure stdlib, no deps.
#
# It serves a small page with a "Do something" button. EVERY request is written
# as one JSON line to /var/log/webapp/access.log (and stdout), which the in-
# container Universal Forwarder ships to Splunk (index=berlin_web,
# sourcetype=webapp:access). The client's source IP is the key that correlates
# with the Windows logon that opened the page.
#
#   PORT (default 8080) and LOG (default /var/log/webapp/access.log) via env.
# ===========================================================================
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("WEBAPP_PORT", "8080"))
LOG = os.environ.get("WEBAPP_LOG", "/var/log/webapp/access.log")

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>dCloud Web-App</title>
<style>body{{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;max-width:640px;margin:40px auto;padding:0 16px}}
h1{{color:#58a6ff}} input,button{{font-size:16px;padding:8px;border-radius:6px;border:1px solid #30363d;background:#161b22;color:#e6edf3}}
button{{background:#238636;border:0;cursor:pointer}} .card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:20px;margin-top:16px}}
code{{color:#8b949e}}</style></head><body>
<h1>dCloud Web-App</h1>
<p>Running in an LXC container on the Berlin Proxmox hypervisor. Every click is
logged and correlated in Splunk with your Windows session.</p>
<div class="card">
  <form method="POST" action="/action">
    <label>Your name: <input name="name" value="{name}" placeholder="e.g. leo"/></label>
    <button type="submit">Do something</button>
  </form>
  <p><code>{msg}</code></p>
</div>
<div class="card">Served by <b>{host}</b> · port <b>{port}</b> · {ts}</div>
</body></html>"""


def log_event(handler, status, length, extra=None):
    xff = handler.headers.get("X-Forwarded-For", "")
    ev = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "src_ip": (xff.split(",")[0].strip() if xff else handler.client_address[0]),
        "client_ip": handler.client_address[0],
        "method": handler.command,
        "path": urlparse(handler.path).path,
        "status": status,
        "bytes": length,
        "user_agent": handler.headers.get("User-Agent", ""),
        "host": os.uname().nodename,
        "port": PORT,
    }
    if extra:
        ev.update(extra)
    line = json.dumps(ev)
    try:
        with open(LOG, "a") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass
    print(line, flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "dcloud-webapp/1.0"

    def _page(self, name="", msg="Ready."):
        body = PAGE.format(name=name, msg=msg, host=os.uname().nodename, port=PORT,
                           ts=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return len(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/healthz":
            msg = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            log_event(self, 200, len(msg))
            return
        n = self._page()
        log_event(self, 200, n)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        name = (parse_qs(raw).get("name", [""])[0]) or ""
        n = self._page(name=name, msg="Action performed for '%s' at %s." %
                       (name or "(anonymous)", time.strftime("%H:%M:%S", time.gmtime())))
        log_event(self, 200, n, extra={"action": "do_something", "name": name})

    def log_message(self, *args):  # silence default stderr logging; we log JSON
        return


if __name__ == "__main__":
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    print("dcloud-webapp listening on 0.0.0.0:%d, logging to %s" % (PORT, LOG), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
