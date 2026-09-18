#!/usr/bin/env python3
# ===========================================================================
# seed_predictive_history.py - backfill synthetic ITSI health-score history
# into the `itsi_summary` index so ITSI Predictive Analytics has something to
# TRAIN a model on immediately, instead of waiting ~14 days for real history.
#
# WHY THIS EXISTS
#   ITSI Predictive Analytics predicts a service's health score 30 min ahead
#   using MLTK, trained on the service's HISTORICAL health score + KPI values
#   (stored in index=itsi_summary). A reset-each-session lab starts with an
#   empty itsi_summary, so the "Select a model" dropdown stays empty and
#   training says "insufficient data". This script writes a couple of weeks of
#   plausible per-KPI + aggregate ServiceHealthScore rows, backdated, so you
#   can train a model right after seeding services.
#
# WHAT IT DOES
#   1. Reads the seeded services (and their KPIs) from the ITSI REST API - so
#      it uses the REAL service _keys / KPI ids, never hardcoded ones.
#   2. Generates a synthetic time series per service (diurnal pattern + noise +
#      a few injected "incident" dips) over --days at --interval-minutes.
#   3. Streams the events into index=itsi_summary via receivers/stream, each
#      line backdated with an ISO8601 timestamp Splunk parses into _time.
#
# RUN IT (on the Splunk box, AFTER seed_itsi_demo.py has created the services):
#   sudo -u splunk /opt/splunk/bin/splunk cmd python3 \
#     /opt/dcloud-splunk/itsi/seed_predictive_history.py \
#     --user admin --password C1sco12345 --days 14 --verbose
#   # preview without writing:  ... --dry-run
#
# BEST-EFFORT / VERSION-SENSITIVE
#   The exact itsi_summary field set ITSI Predictive Analytics trains on shifts
#   between ITSI versions. If training still reports no data after this, run
#   with --verbose and compare the fields below against a REAL health-score row
#   once one exists:
#     index=itsi_summary is_service_aggregate=1 | head 1
#   then adjust FIELDS_AGG / FIELDS_KPI. Nothing here is destructive - it only
#   appends events to itsi_summary (delete them with a piped `| delete` if a run
#   went wrong, as admin).
# ===========================================================================
import argparse
import base64
import json
import math
import os
import random
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib import request, parse, error

APP_NS = "servicesNS/nobody/itsi"
SUMMARY_INDEX = "itsi_summary"
SOURCETYPE = "stash"                 # ITSI summary events use the stash sourcetype
SOURCE = "predictive_backfill"

# ITSI severity bands (value -> (level, label)) keyed by health score, high=good.
def sev_for_health(h):
    if h >= 80:
        return 2, "normal"
    if h >= 60:
        return 3, "low"
    if h >= 40:
        return 4, "medium"
    if h >= 20:
        return 5, "high"
    return 6, "critical"


class ITSI:
    def __init__(self, base, user, password, verbose=False):
        self.base = base.rstrip("/")
        self.verbose = verbose
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        self.auth = "Basic " + base64.b64encode(("%s:%s" % (user, password)).encode()).decode()

    def call(self, method, path, params=None, body=None, raw_body=None):
        url = "%s/%s" % (self.base, path.lstrip("/"))
        if params:
            url += "?" + parse.urlencode(params)
        data = None
        headers = {"Authorization": self.auth}
        if raw_body is not None:
            data = raw_body.encode("utf-8")
            headers["Content-Type"] = "text/plain; charset=utf-8"
        elif body is not None:
            data = parse.urlencode(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            with request.urlopen(request.Request(url, data=data, headers=headers, method=method),
                                 timeout=120, context=self.ctx) as r:
                txt = r.read().decode("utf-8", "replace")
                return r.status, (json.loads(txt) if txt.strip().startswith(("{", "[")) else txt)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            if self.verbose:
                sys.stderr.write("  HTTP %s %s -> %s\n" % (exc.code, path, detail))
            return exc.code, detail

    def services(self):
        # Only real (non-rollup) health matters most, but we backfill all so the
        # dropdown offers every service. Pull _key, title and kpis.
        code, res = self.call("GET", "%s/itoa_interface/service" % APP_NS,
                              params={"fields": "_key,title,kpis"})
        if code != 200 or not isinstance(res, list):
            sys.stderr.write("ERROR: could not list services (HTTP %s). Is ITSI up and seeded?\n" % code)
            return []
        return res

    def send(self, lines):
        # receivers/stream: one POST, many newline-delimited events; each event's
        # _time comes from its leading ISO8601 timestamp (Splunk auto-detects it).
        return self.call("POST", "services/receivers/stream",
                         params={"index": SUMMARY_INDEX, "sourcetype": SOURCETYPE, "source": SOURCE},
                         raw_body="\n".join(lines) + "\n")


def kv(**fields):
    parts = []
    for k, v in fields.items():
        if isinstance(v, str):
            parts.append('%s="%s"' % (k, v.replace('"', "'")))
        else:
            parts.append("%s=%s" % (k, v))
    return " ".join(parts)


def synth_health(ts, day_phase, incident):
    # Base ~92, gentle diurnal dip in the afternoon, noise, and a hard drop
    # during an injected incident window. Clamp to 0..100.
    base = 92 + 4 * math.sin(day_phase * 2 * math.pi)
    val = base + random.uniform(-4, 4)
    if incident:
        val = random.uniform(18, 55)
    return max(0.0, min(100.0, round(val, 1)))


def kpi_value(kpi_title, health, incident):
    t = kpi_title.lower()
    if "reach" in t:                       # down% metric: 0 good, 100 down
        return 100.0 if incident else 0.0
    if "cpu" in t or "memory" in t or "disk" in t or "latency" in t:
        base = (100 - health) * 0.6        # loosely track (inverse of) health
        return max(0.0, min(100.0, round(base + random.uniform(-8, 12), 1)))
    # counts / volumes: some positive number, lower during an incident
    return round(random.uniform(5, 60) * (0.2 if incident else 1.0), 0)


def build_events(services, days, interval_min, seed=7):
    random.seed(seed)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = now - timedelta(days=days)
    step = timedelta(minutes=interval_min)
    # A few incident windows across the range (start_offset_hours, duration_hours).
    total_hours = days * 24
    incidents = [(random.uniform(0, total_hours), random.uniform(1, 4)) for _ in range(max(3, days // 3))]

    def in_incident(ts):
        h = (ts - start).total_seconds() / 3600.0
        return any(s <= h <= s + d for s, d in incidents)

    per_service = {}
    for svc in services:
        skey = svc.get("_key")
        title = svc.get("title", "?")
        kpis = svc.get("kpis") or []
        lines = []
        t = start
        while t <= now:
            iso = t.strftime("%Y-%m-%dT%H:%M:%S%z")
            iso = iso[:-2] + ":" + iso[-2:] if iso else iso   # RFC3339 +00:00
            incident = in_incident(t)
            phase = ((t.hour * 60 + t.minute) / 1440.0)
            health = synth_health(t, phase, incident)
            lvl, sev = sev_for_health(health)
            # aggregate service health score (what Predictive Analytics predicts)
            lines.append("%s %s" % (iso, kv(
                alert_value=health, alert_level=lvl, alert_severity=sev,
                kpi="ServiceHealthScore", kpiid="SHKPI-%s" % skey,
                itsi_service_id=skey, serviceid=skey, service_name=title,
                is_service_aggregate=1, is_service_max_severity_event=1,
                is_service_in_maintenance=0, is_filtered_out=0, urgency=11)))
            # per-KPI feature rows
            for k in kpis:
                kkey = k.get("_key")
                ktitle = k.get("title", "kpi")
                if not kkey:
                    continue
                # ITSI puts each service's reserved health-score KPI (SHKPI-<key>,
                # title "ServiceHealthScore") in its `kpis` list. Skip it here - the
                # aggregate row above already emits the health score; emitting it
                # again as a "feature" would write a bogus random value under it.
                if kkey.startswith("SHKPI") or ktitle.lower().replace(" ", "") == "servicehealthscore":
                    continue
                v = kpi_value(ktitle, health, incident)
                klvl, ksev = sev_for_health(100 - v if "reach" in ktitle.lower() else health)
                lines.append("%s %s" % (iso, kv(
                    alert_value=v, alert_level=klvl, alert_severity=ksev,
                    kpi=ktitle, kpiid=kkey,
                    itsi_service_id=skey, serviceid=skey, service_name=title,
                    is_service_aggregate=0, is_filtered_out=0, urgency=5)))
            t += step
        per_service[title] = lines
    return per_service


def main():
    ap = argparse.ArgumentParser(description="Backfill synthetic ITSI health-score history for Predictive Analytics.")
    ap.add_argument("--host", default=os.environ.get("SPLUNK_MGMT", "https://localhost:8089"))
    ap.add_argument("--user", default=os.environ.get("SPLUNK_ADMIN_USER", "admin"))
    ap.add_argument("--password", default=os.environ.get("SPLUNK_ADMIN_PASSWORD", "C1sco12345"))
    ap.add_argument("--days", type=int, default=14, help="days of history to backfill (default 14)")
    ap.add_argument("--interval-minutes", type=int, default=15, help="sample spacing (default 15)")
    ap.add_argument("--only", default="", help="comma-separated service titles to limit to (default all)")
    ap.add_argument("--dry-run", action="store_true", help="print a summary, write nothing")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    itsi = ITSI(args.host, args.user, args.password, verbose=args.verbose)
    services = itsi.services()
    if not services:
        return 1
    if args.only:
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        services = [s for s in services if s.get("title") in want]
        if not services:
            sys.stderr.write("No services matched --only=%s\n" % args.only)
            return 1

    per_service = build_events(services, args.days, args.interval_minutes)
    total = sum(len(v) for v in per_service.values())
    print("Prepared %d events across %d services (%d days @ %d-min)."
          % (total, len(per_service), args.days, args.interval_minutes))

    if args.dry_run:
        for title, lines in per_service.items():
            print("  %-26s %6d events   e.g. %s" % (title, len(lines), lines[len(lines) // 2][:120]))
        print("\nDRY RUN - nothing written. Drop --dry-run to stream into index=%s." % SUMMARY_INDEX)
        return 0

    rc = 0
    for title, lines in per_service.items():
        # stream in chunks so no single POST is enormous
        sent = 0
        for i in range(0, len(lines), 5000):
            code, res = itsi.send(lines[i:i + 5000])
            if code not in (200, 201, 204):
                rc = 1
                sys.stderr.write("  FAILED %s chunk (HTTP %s): %s\n" % (title, code, str(res)[:300]))
                break
            sent += len(lines[i:i + 5000])
        print("  %-26s wrote %6d events" % (title, sent))
    if rc == 0:
        print("\nDone. Now train a model: ITSI -> Predictive Analytics -> select a service -> "
              "train/create a model (it reads this backfilled history from index=%s)." % SUMMARY_INDEX)
        print("Verify the data landed:  index=%s source=%s is_service_aggregate=1 | stats count by service_name"
              % (SUMMARY_INDEX, SOURCE))
    return rc


if __name__ == "__main__":
    sys.exit(main())
