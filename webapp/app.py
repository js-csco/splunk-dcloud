#!/usr/bin/env python3
# ===========================================================================
# app.py - the "Directory App": a small employee directory / org chart that
# runs in an LXC container on Proxmox (Berlin) and stores its data in the
# PostgreSQL container (db-berlin). Pure stdlib HTTP server; psycopg2 for the DB.
#
# What it demonstrates for the Splunk/ITSI story:
#  1) A real two-tier app: WEB tier (this container) + DB tier (db-berlin). The
#     org chart is read from Postgres; adding an employee writes to Postgres.
#  2) FULL request capture - every request is one JSON line in
#     /var/log/webapp/access.log -> Splunk (index=berlin_web,
#     sourcetype=webapp:access): src_ip, method, path, ALL headers, body preview,
#     X-Forwarded-For chain. Adding an employee also records the client src_ip.
#  3) /healthz returns 200 only if the DB is reachable, else 503 - so one probe
#     reflects the whole Directory App (web + db) chain, which ITSI rolls up.
#
# Env: WEBAPP_PORT (8080), WEBAPP_LOG (/var/log/webapp/access.log),
#      DB_HOST (198.18.3.51), DB_PORT (5432), DB_NAME (demo), DB_USER (demo),
#      DB_PASS (C1sco12345).
# ===========================================================================
import html
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

CSS = """
body{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:0 16px 40px}
.wrap{max-width:1100px;margin:0 auto}
h1{color:#58a6ff;margin:24px 0 2px} .sub{color:#8b949e;margin:0 0 18px}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px 18px;margin-bottom:18px}
input,select,button{font-size:14px;padding:8px 10px;border-radius:6px;border:1px solid #30363d;background:#0d1117;color:#e6edf3}
button{background:#238636;border:0;cursor:pointer;font-weight:600}
.row{display:flex;flex-wrap:wrap;gap:10px;align-items:end}
.field{display:flex;flex-direction:column;gap:4px} .field label{font-size:12px;color:#8b949e}
.chart{display:flex;flex-wrap:wrap;gap:16px}
.team{flex:1 1 220px;background:#0d1117;border:1px solid #30363d;border-radius:10px;padding:12px}
.team h3{margin:0 0 10px;color:#e6edf3;font-size:15px;display:flex;justify-content:space-between}
.team h3 .n{color:#8b949e;font-weight:400;font-size:12px}
.emp{background:#161b22;border:1px solid #30363d;border-left:3px solid #58a6ff;border-radius:8px;padding:8px 10px;margin-bottom:8px}
.emp .nm{font-weight:600} .emp .ti{color:#8b949e;font-size:12px} .emp .em{color:#6e7681;font-size:11px}
.ok{color:#3fb950} .bad{color:#f85149} .msg{color:#8b949e;font-size:13px}
.foot{color:#6e7681;font-size:12px;margin-top:8px}
"""


def db_connect():
    import psycopg2  # lazy import so the app still starts if psycopg2 is missing
    return psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                            user=DB_USER, password=DB_PASS, connect_timeout=3)


def db_status():
    try:
        c = db_connect(); cur = c.cursor()
        cur.execute("SELECT count(*) FROM employees")
        n = cur.fetchone()[0]; c.close()
        return True, n, ""
    except Exception as exc:  # noqa: BLE001
        return False, 0, str(exc)


def list_teams():
    c = db_connect(); cur = c.cursor()
    cur.execute("SELECT id, name FROM teams ORDER BY id")
    rows = cur.fetchall(); c.close()
    return rows


def org_data():
    """Return list of (team_name, [ (name,title,email), ... ]) ordered by team."""
    c = db_connect(); cur = c.cursor()
    cur.execute("""SELECT t.name, e.name, e.title, e.email
                   FROM teams t LEFT JOIN employees e ON e.team_id = t.id
                   ORDER BY t.id, e.name""")
    rows = cur.fetchall(); c.close()
    teams = {}
    for tname, ename, title, email in rows:
        teams.setdefault(tname, [])
        if ename:
            teams[tname].append((ename, title or "", email or ""))
    return list(teams.items())


def add_employee(name, title, team_id, email, src_ip):
    c = db_connect(); cur = c.cursor()
    cur.execute("""INSERT INTO employees(name, title, team_id, email, created_src_ip)
                   VALUES (%s, %s, %s, %s, %s)""",
                (name, title, int(team_id), email, src_ip))
    c.commit(); c.close()


def render(msg=""):
    ok, count, err = db_status()
    parts = ['<!doctype html><html><head><meta charset="utf-8"><title>Directory App</title>',
             "<style>%s</style></head><body><div class='wrap'>" % CSS,
             "<h1>Directory App</h1>",
             "<p class='sub'>Employee directory &amp; org chart · web tier on Proxmox, "
             "data in PostgreSQL (db-berlin)</p>"]

    # add-employee form
    if ok:
        opts = "".join("<option value='%d'>%s</option>" % (tid, html.escape(tn))
                       for tid, tn in list_teams())
        parts.append(
            "<div class='card'><form method='POST' action='/add'><div class='row'>"
            "<div class='field'><label>Name</label><input name='name' required placeholder='Jane Doe'/></div>"
            "<div class='field'><label>Title</label><input name='title' placeholder='Engineer'/></div>"
            "<div class='field'><label>Team</label><select name='team_id'>%s</select></div>"
            "<div class='field'><label>Email</label><input name='email' placeholder='jane@dcloud.demo'/></div>"
            "<button type='submit'>Add employee</button></div></form>"
            "<p class='msg'>%s</p></div>" % (opts, html.escape(msg) if msg else "Add a person and they appear in their team below."))
    else:
        parts.append("<div class='card'><b class='bad'>Database unavailable</b> — the DB tier "
                     "(db-berlin) can't be reached, so the directory can't load. "
                     "<span class='msg'>%s</span></div>" % html.escape(err[:200]))

    # org chart
    if ok:
        parts.append("<div class='chart'>")
        for tname, emps in org_data():
            parts.append("<div class='team'><h3>%s<span class='n'>%d</span></h3>"
                         % (html.escape(tname), len(emps)))
            if emps:
                for ename, title, email in emps:
                    parts.append("<div class='emp'><div class='nm'>%s</div>"
                                 "<div class='ti'>%s</div><div class='em'>%s</div></div>"
                                 % (html.escape(ename), html.escape(title), html.escape(email)))
            else:
                parts.append("<div class='msg'>— no one yet —</div>")
            parts.append("</div>")
        parts.append("</div>")

    parts.append("<p class='foot'>Served by <b>%s</b> · port %d · DB <b class='%s'>%s</b> · "
                 "%d employees · %s</p>" % (os.uname().nodename, PORT,
                 "ok" if ok else "bad", "connected" if ok else "unavailable", count,
                 time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())))
    parts.append("</div></body></html>")
    return "".join(parts).encode()


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
    server_version = "dcloud-directory/2.0"

    def _send_html(self, body, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return len(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/healthz":
            ok, n, err = db_status()
            code = 200 if ok else 503
            msg = ("ok employees=%d" % n if ok else "db_unavailable: %s" % err).encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            log_event(self, code, len(msg), extra={"action": "healthz", "db_ok": ok})
            return
        n = self._send_html(render())
        log_event(self, 200, n, extra={"action": "view_directory"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        form = parse_qs(raw)
        path = urlparse(self.path).path
        src_ip = (self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                  or self.client_address[0])

        if path == "/add":
            name = (form.get("name", [""])[0]).strip()
            title = (form.get("title", [""])[0]).strip()
            email = (form.get("email", [""])[0]).strip()
            team_id = (form.get("team_id", [""])[0]).strip()
            if not name or not team_id:
                n = self._send_html(render(msg="Name and team are required."))
                log_event(self, 200, n, extra={"action": "add_employee", "ok": False,
                                               "reason": "missing fields"}, body=raw)
                return
            try:
                add_employee(name, title, team_id, email, src_ip)
                n = self._send_html(render(msg="Added %s to the directory." % name))
                log_event(self, 200, n, extra={"action": "add_employee", "ok": True,
                                               "employee": name, "title": title,
                                               "team_id": team_id, "src_ip": src_ip}, body=raw)
            except Exception as exc:  # noqa: BLE001
                n = self._send_html(render(msg="Add failed: %s" % exc))
                log_event(self, 200, n, extra={"action": "add_employee", "ok": False,
                                               "error": str(exc)}, body=raw)
            return

        # unknown POST -> just re-render
        n = self._send_html(render())
        log_event(self, 200, n, extra={"action": "post_other"}, body=raw)

    def log_message(self, *args):  # we emit our own JSON log
        return


if __name__ == "__main__":
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    print("dcloud-directory listening on 0.0.0.0:%d, logging to %s, db=%s:%d/%s" %
          (PORT, LOG, DB_HOST, DB_PORT, DB_NAME), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
