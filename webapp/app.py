#!/usr/bin/env python3
# ===========================================================================
# app.py - demo web-app for the "Client -> App -> Hypervisor" story. Runs in an
# LXC container on Proxmox (Berlin). Pure stdlib for the HTTP server; psycopg2
# (optional) for the PostgreSQL button.
#
# Two things it demonstrates:
#  1) FULL request capture - every request is written as one JSON line to
#     /var/log/webapp/access.log (shipped to Splunk: index=berlin_web,
#     sourcetype=webapp:access). We record src_ip + the whole request (method,
#     path, query, ALL headers, body preview, X-Forwarded-For chain).
#  2) "Save entry to database" button -> INSERT into PostgreSQL on db-berlin,
#     recording who wrote it (src_ip, name, note, user-agent, time). So the same
#     src_ip appears in the web log AND in the DB row.
#
# /healthz returns 200 only if the app can reach the DB, else 503 - so a single
# probe reflects the whole app+db chain.
#
# Env: WEBAPP_PORT (8080), WEBAPP_LOG (/var/log/webapp/access.log),
#      DB_HOST (198.18.3.51), DB_PORT (5432), DB_NAME (demo), DB_USER (demo),
#      DB_PASS (C1sco12345).
# ===========================================================================
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("WEBAPP_PORT", "8080"))
LOG = os.environ.get("WEBAPP_LOG", "/var/log/webapp/access.log")
DB_HOST = os.environ.get("DB_HOST", "198.18.3.51")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "demo")
DB_USER = os.environ.get("DB_USER", "demo")
DB_PASS = os.environ.get("DB_PASS", "C1sco12345")

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>dCloud Web-App</title>
<style>body{{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;max-width:680px;margin:40px auto;padding:0 16px}}
h1{{color:#58a6ff}} input,button{{font-size:16px;padding:8px;border-radius:6px;border:1px solid #30363d;background:#161b22;color:#e6edf3}}
button{{background:#238636;border:0;cursor:pointer}} .db{{background:#1f6feb}} .card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:20px;margin-top:16px}}
code{{color:#8b949e}} .ok{{color:#3fb950}} .bad{{color:#f85149}}</style></head><body>
<h1>dCloud Web-App</h1>
<p>Running in an LXC container on the Berlin Proxmox hypervisor. Every click is
logged and correlated in Splunk with your client session.</p>
<div class="card">
  <form method="POST" action="/action">
    <label>Your name: <input name="name" value="{name}" placeholder="e.g. leo"/></label>
    <button type="submit">Do something</button>
  </form>
  <p><code>{msg}</code></p>
</div>
<div class="card">
  <form method="POST" action="/db">
    <label>Note: <input name="note" value="" placeholder="a line to store"/></label>
    <input type="hidden" name="name" value="{name}"/>
    <button class="db" type="submit">Save entry to database</button>
  </form>
  <p>Database (db-berlin): <b class="{dbclass}">{dbstate}</b> · rows stored: <b>{dbrows}</b></p>
</div>
<div class="card">Served by <b>{host}</b> · port <b>{port}</b> · {ts}</div>
</body></html>"""


def db_connect():
    import psycopg2  # imported lazily so the app runs even before psycopg2 is installed
    return psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                            user=DB_USER, password=DB_PASS, connect_timeout=3)


def db_status():
    """Return (ok, rowcount, error)."""
    try:
        c = db_connect(); cur = c.cursor()
        cur.execute("SELECT count(*) FROM entries")
        n = cur.fetchone()[0]
        c.close()
        return True, n, ""
    except Exception as exc:  # noqa: BLE001
        return False, 0, str(exc)


def db_insert(src_ip, name, note, ua):
    c = db_connect(); cur = c.cursor()
    cur.execute(
        "INSERT INTO entries(ts, src_ip, name, note, user_agent) VALUES (now(), %s, %s, %s, %s)",
        (src_ip, name, note, ua))
    c.commit()
    cur.execute("SELECT count(*) FROM entries")
    n = cur.fetchone()[0]
    c.close()
    return n


def log_event(handler, status, length, extra=None, body=""):
    xff = handler.headers.get("X-Forwarded-For", "")
    parsed = urlparse(handler.path)
    ev = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "src_ip": (xff.split(",")[0].strip() if xff else handler.client_address[0]),
        "client_ip": handler.client_address[0],
        "xff_chain": xff,
        "method": handler.command,
        "path": parsed.path,
        "query": parsed.query,
        "status": status,
        "bytes": length,
        "referer": handler.headers.get("Referer", ""),
        "user_agent": handler.headers.get("User-Agent", ""),
        # FULL request headers, so the whole request the app saw is captured.
        "headers": {k: v for k, v in handler.headers.items()},
        "host": os.uname().nodename,
        "port": PORT,
    }
    if body:
        ev["body"] = body[:512]
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
    server_version = "dcloud-webapp/1.1"

    def _page(self, name="", msg="Ready."):
        ok, rows, _ = db_status()
        body = PAGE.format(
            name=name, msg=msg, host=os.uname().nodename, port=PORT,
            dbstate=("connected" if ok else "unavailable"),
            dbclass=("ok" if ok else "bad"), dbrows=rows,
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
            ok, rows, err = db_status()
            code = 200 if ok else 503
            msg = ("ok db_rows=%d" % rows).encode() if ok else ("db_unavailable: %s" % err).encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            log_event(self, code, len(msg), extra={"action": "healthz", "db_ok": ok})
            return
        n = self._page()
        log_event(self, 200, n)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        form = parse_qs(raw)
        name = (form.get("name", [""])[0]) or ""
        path = urlparse(self.path).path
        src_ip = (self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                  or self.client_address[0])

        if path == "/db":
            note = (form.get("note", [""])[0]) or ""
            ua = self.headers.get("User-Agent", "")
            try:
                rows = db_insert(src_ip, name, note, ua)
                msg = "Saved to DB (row #%d) for '%s' from %s." % (rows, name or "(anonymous)", src_ip)
                n = self._page(name=name, msg=msg)
                log_event(self, 200, n, extra={"action": "db_write", "db_ok": True,
                                               "name": name, "note": note, "db_rows": rows}, body=raw)
            except Exception as exc:  # noqa: BLE001
                n = self._page(name=name, msg="DB write FAILED: %s" % exc)
                log_event(self, 200, n, extra={"action": "db_write", "db_ok": False,
                                               "name": name, "error": str(exc)}, body=raw)
            return

        # default: /action
        n = self._page(name=name, msg="Action performed for '%s' at %s." %
                       (name or "(anonymous)", time.strftime("%H:%M:%S", time.gmtime())))
        log_event(self, 200, n, extra={"action": "do_something", "name": name}, body=raw)

    def log_message(self, *args):  # silence default stderr logging; we log JSON
        return


if __name__ == "__main__":
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    print("dcloud-webapp listening on 0.0.0.0:%d, logging to %s, db=%s:%d/%s" %
          (PORT, LOG, DB_HOST, DB_PORT, DB_NAME), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
