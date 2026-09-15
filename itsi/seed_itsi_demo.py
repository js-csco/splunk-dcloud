#!/usr/bin/env python3
# ===========================================================================
# seed_itsi_demo.py - populate IT Essentials Work / ITSI with demo content.
#
# ITE-W / ITSI ships EMPTY: installing the app gives you the engine but no
# Entities, Services, or KPIs. Unlike the rest of this lab (plain config files
# that apply.sh rebuilds every session), ITSI's content lives in the KV store
# and is created through the ITSI REST API - which is what this script does.
#
# It creates, idempotently (matched by title):
#   * Entities   - the lab hosts, tagged with host + site.
#   * Services   - "London Infrastructure", "Berlin Infrastructure",
#                  "Web Service (Berlin)" - each with an entity rule so the
#                  right hosts attach automatically.
#   * KPIs       - ad-hoc KPIs over data already flowing in the lab
#                  (CPU %, Memory %, HTTP request volume).
#
# RUN IT ONCE, AFTER ITE-W IS INSTALLED AND SPLUNK HAS RESTARTED:
#   sudo -u splunk /opt/splunk/bin/splunk cmd python3 \
#     /opt/dcloud-splunk/itsi/seed_itsi_demo.py --user admin --password C1sco12345
# (or from anywhere with network to the box: --host https://198.18.1.124:8089)
#
# NOTE: use `splunk cmd python3`, NOT `/opt/splunk/bin/python3` directly - the
# latter picks up the system OpenSSL and fails to import ssl. `splunk cmd` sets
# Splunk's LD_LIBRARY_PATH so the bundled Python's ssl loads. (System /usr/bin/
# python3 also works, since this script only uses the standard library.)
#
# CAVEAT: ITSI's REST schema shifts between versions. This targets the 4.x/5.x
# itoa_interface API. If the server rejects an object, run with --verbose to see
# the exact error body, then adjust the MODEL / KPI_TEMPLATE below. Entities and
# Services are the stable part; KPIs are the version-sensitive part.
# ===========================================================================
import argparse
import json
import os
import ssl
import sys
import uuid
from urllib import request, parse, error

SEC_GRP = "default_itsi_security_group"   # ITSI's built-in "Global" team
APP_NS = "servicesNS/nobody/itsi"         # ITSI objects live in the itsi app

# --- The demo model --------------------------------------------------------
# Entities to register. (host, site, role, description)
ENTITIES = [
    ("desktop-london",  "london", "client",     "Linux desktop client (London)"),
    ("ubuntu-london",   "london", "server",     "Ubuntu server (London)"),
    ("cat8kv-london",   "london", "router",     "Cisco Catalyst 8000v router (London)"),
    ("proxmox-berlin",  "berlin", "hypervisor", "Proxmox hypervisor (Berlin)"),
    ("webapp-berlin",   "berlin", "webserver",  "Web-app container (Berlin)"),
    ("db-berlin",       "berlin", "database",   "PostgreSQL container (Berlin)"),
    ("ubuntu-berlin",   "berlin", "server",     "Ubuntu server (Berlin)"),
    ("cat8kv-berlin",   "berlin", "router",     "Cisco Catalyst 8000v router (Berlin)"),
    ("splunk",          "loc1",   "splunk",     "Splunk server (Location 1)"),
]

# KPI spec: (title, base_search, threshold_field, aggregate, unit, medium, critical)
#   medium/critical are ascending thresholds, tuned so normal lab activity reads
#   green and a spike trips warning/critical.
#
# LEAF services: (title, description, rule_field, rule_value, [kpi specs])
#   rule_field is an entity info field ("site" or "role"); the service auto-attaches
#   every entity whose that field matches rule_value.
LEAF_SERVICES = [
    ("Splunk Core (loc1)", "The Splunk server itself (Location 1).", "role", "splunk", [
        ("CPU Utilization",       "index=loc1_metrics sourcetype=linux:metrics", "cpu_pct",      "avg",   "%",      70, 90),
        ("Memory Utilization",    "index=loc1_metrics sourcetype=linux:metrics", "mem_used_pct", "avg",   "%",      70, 90),
        ("Internal Event Volume", "index=_internal",                             "count",        "count", "events", 800000, 2000000),
    ]),
    ("London Infrastructure", "Health of the London site hosts.", "site", "london", [
        ("CPU Utilization",    "index=london_metrics sourcetype=linux:metrics", "cpu_pct",      "avg", "%", 70, 90),
        ("Memory Utilization", "index=london_metrics sourcetype=linux:metrics", "mem_used_pct", "avg", "%", 70, 90),
    ]),
    ("Berlin Infrastructure", "Health of the Berlin site hosts.", "site", "berlin", [
        ("CPU Utilization",    "index=berlin_metrics sourcetype=linux:metrics", "cpu_pct",      "avg", "%", 70, 90),
        ("Memory Utilization", "index=berlin_metrics sourcetype=linux:metrics", "mem_used_pct", "avg", "%", 70, 90),
    ]),
    ("Hypervisor (Proxmox)", "The Berlin Proxmox hypervisor.", "role", "hypervisor", [
        ("Proxmox Event Volume", "index=berlin_proxmox", "count", "count", "events", 50000, 200000),
    ]),
    ("Web Service (Berlin)", "The Berlin web application.", "role", "webserver", [
        # Reachability: port probe -> down=0 when up, 100 when unreachable. This is
        # what turns the service RED in the failure-injection demo (HTTP volume
        # alone can't - zero traffic reads as green).
        ("App Reachability",    "index=berlin_web sourcetype=port:probe port=8080 | eval down=if(open==1,0,100)", "down", "max", "%", 1, 50),
        ("HTTP Request Volume", "index=berlin_web sourcetype=webapp:access",             "count", "count", "req",    20000, 80000),
        ("HTTP Errors (5xx)",   "index=berlin_web sourcetype=webapp:access status>=500", "count", "count", "errors", 5,     25),
    ]),
    ("Database Service (Berlin)", "The Berlin PostgreSQL database.", "role", "database", [
        ("DB Reachability", "index=berlin_web sourcetype=port:probe port=5432 | eval down=if(open==1,0,100)", "down", "max", "%", 1, 50),
        ("DB Log Volume",   "index=berlin_db sourcetype=postgres:log", "count", "count", "events", 20000, 80000),
    ]),
    ("Network & Routers", "The Cisco Catalyst routers (London + Berlin).", "role", "router", [
        ("Router Poll Volume", "index=london_network OR index=berlin_network", "count", "count", "events", 100000, 500000),
    ]),
]

# Parent service - rolls up every leaf service into one health tree.
PARENT_SERVICE = ("Global IT Operations", "Top-level rollup of every lab service.")

# ITSI severity palette (value/label/colors) used to build thresholds.
SEV = {
    "normal":   (2, "#99D18B", "#DCEFD7"),
    "medium":   (4, "#FCB64E", "#FEE6C1"),
    "critical": (6, "#B50101", "#E5A6A6"),
}


class ITSI:
    def __init__(self, base, user, password, verbose=False):
        self.base = base.rstrip("/")
        self.verbose = verbose
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        self._auth = "Basic " + _b64("%s:%s" % (user, password))

    def _call(self, method, path, params=None, body=None):
        url = "%s/%s" % (self.base, path.lstrip("/"))
        data = None
        headers = {"Authorization": self._auth}
        if params:
            url += "?" + parse.urlencode(params)
        if body is not None:
            data = parse.urlencode(body).encode() if isinstance(body, dict) else body.encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=30, context=self.ctx) as r:
                raw = r.read().decode()
                return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
        except error.HTTPError as exc:
            detail = exc.read().decode()[:600]
            if self.verbose:
                sys.stderr.write("  HTTP %s %s -> %s\n" % (exc.code, path, detail))
            return exc.code, detail

    # itoa_interface object helpers ----------------------------------------
    def find(self, obj, title):
        code, res = self._call("GET", "%s/itoa_interface/%s" % (APP_NS, obj),
                               params={"filter": json.dumps({"title": title}), "fields": "_key,title"})
        if code == 200 and isinstance(res, list) and res:
            return res[0].get("_key")
        return None

    def upsert(self, obj, title, payload):
        key = self.find(obj, title)
        payload = dict(payload, title=title, sec_grp=SEC_GRP)
        if key:
            payload["_key"] = key
            code, res = self._call("POST", "%s/itoa_interface/%s/%s" % (APP_NS, obj, key),
                                   body={"data": json.dumps(payload)})
            action = "updated"
        else:
            code, res = self._call("POST", "%s/itoa_interface/%s" % (APP_NS, obj),
                                   body={"data": json.dumps(payload)})
            action = "created"
        ok = code in (200, 201)
        if ok and not key and isinstance(res, dict):
            key = res.get("_key")
        print("  %-9s %-8s %-28s %s" % (action if ok else "FAILED", obj, title,
                                        "" if ok else ("(HTTP %s)" % code)))
        return key if ok else None


def _b64(s):
    import base64
    return base64.b64encode(s.encode()).decode()


def entity_payload(host, site, role, desc):
    # ITSI entity: identifier/informational fields must ALSO be present as
    # top-level list keys (host, site, role).
    return {
        "description": desc,
        "identifier": {"fields": ["host"], "values": [host]},
        "informational": {"fields": ["site", "role"], "values": [site, role]},
        "host": [host],
        "site": [site],
        "role": [role],
        "entity_type_ids": [],
    }


def thresholds(field, medium, critical):
    n_v, n_c, n_cl = SEV["normal"]
    levels = []
    for label, val in (("medium", medium), ("critical", critical)):
        if val and val > 0:
            sv, sc, scl = SEV[label]
            levels.append({"dynamicParam": "", "severityColor": sc, "severityColorLight": scl,
                           "severityLabel": label, "severityValue": sv, "thresholdValue": val})
    gmax = max(100, (critical or 100) * 1.2)
    return {
        "baseSeverityColor": n_c, "baseSeverityColorLight": n_cl,
        "baseSeverityLabel": "normal", "baseSeverityValue": n_v,
        "gaugeMax": gmax, "gaugeMin": 0,
        "isMaxStatic": False, "isMinStatic": True,
        "metricField": field, "renderBoundaryMax": gmax, "renderBoundaryMin": 0,
        "search": "", "thresholdLevels": levels,
    }


def kpi_payload(title, base_search, field, agg, unit, medium, critical):
    return {
        "_key": str(uuid.uuid4()),
        "title": title,
        "urgency": 5,
        "search_type": "adhoc",
        "base_search": base_search,
        "threshold_field": field,
        "unit": unit,
        "search_aggregate": agg,
        "entity_statop": agg,
        "aggregate_statop": agg,
        "is_service_entity_filter": True,
        "entity_breakdown_id_fields": "host",
        "entity_id_fields": "host",
        "is_entity_breakdown": False,
        "fill_gaps": "null_value",
        "gap_severity": "unknown",
        "alert_period": "5",
        "alert_lag": "30",
        "search_alert_earliest": "5",
        # Backfill so KPI values (and health colours) appear immediately instead
        # of only after the scheduled searches have run for a while.
        "backfill_enabled": True,
        "backfill_earliest_time": "-24h",
        "time_variate_thresholds": False,
        "adaptive_thresholds_is_enabled": False,
        "adaptive_thresholding_training_window": "-7d",
        "kpi_threshold_template_id": "",
        "kpi_base_search": "",
        "source": "service_kpi",
        "aggregate_thresholds": thresholds(field, medium, critical),
        "entity_thresholds": thresholds(field, medium, critical),
    }


def service_payload(desc, rule_field, rule_value, kpis, depends_on=None):
    svc = {
        "description": desc,
        "enabled": 1,
        "entity_rules": [{
            "rule_condition": "AND",
            "rule_items": [{"field": rule_field, "field_type": "info",
                            "rule_type": "matches", "value": rule_value}],
        }],
        "kpis": kpis,
    }
    if depends_on:
        svc["services_depends_on"] = depends_on
    return svc


def parent_payload(desc, depends_on):
    # A rollup service: no entities of its own, health derived from the health
    # scores of the services it depends on.
    return {
        "description": desc,
        "enabled": 1,
        "entity_rules": [],
        "kpis": [],
        "services_depends_on": depends_on,
    }


def main():
    ap = argparse.ArgumentParser(description="Seed ITE-W / ITSI with demo entities, services and KPIs.")
    ap.add_argument("--host", default=os.environ.get("SPLUNK_MGMT", "https://localhost:8089"),
                    help="Splunk management URI (default https://localhost:8089)")
    ap.add_argument("--user", default=os.environ.get("SPLUNK_ADMIN_USER", "admin"))
    ap.add_argument("--password", default=os.environ.get("SPLUNK_ADMIN_PASSWORD", "C1sco12345"))
    ap.add_argument("--verbose", action="store_true", help="print server error bodies")
    ap.add_argument("--dry-run", action="store_true", help="print what would be created, call nothing")
    args = ap.parse_args()

    if args.dry_run:
        print("DRY RUN - would create %d entities, %d leaf services + 1 rollup:"
              % (len(ENTITIES), len(LEAF_SERVICES)))
        for h, s, role, d in ENTITIES:
            print("  entity  %-16s site=%-7s role=%s" % (h, s, role))
        for t, d, rf, rv, kpis in LEAF_SERVICES:
            print("  service %-26s %s=%-10s KPIs: %s" % (t, rf, rv, ", ".join(k[0] for k in kpis)))
        print("  service %-26s depends on all %d leaf services" % (PARENT_SERVICE[0], len(LEAF_SERVICES)))
        return 0

    itsi = ITSI(args.host, args.user, args.password, verbose=args.verbose)

    # Preflight: is ITSI reachable?
    code, _ = itsi._call("GET", "%s/itoa_interface/service" % APP_NS, params={"count": "1"})
    if code != 200:
        sys.stderr.write("ERROR: ITSI REST not reachable (HTTP %s at %s). Is ITE-W installed and "
                         "Splunk restarted? Are the admin creds correct?\n" % (code, args.host))
        return 1

    print("Seeding ITSI entities ...")
    for host, site, role, desc in ENTITIES:
        itsi.upsert("entity", host, entity_payload(host, site, role, desc))

    print("Seeding ITSI leaf services + KPIs ...")
    leaf_keys = []
    for title, desc, rf, rv, kpis in LEAF_SERVICES:
        kpi_objs = [kpi_payload(*k) for k in kpis]
        key = itsi.upsert("service", title, service_payload(desc, rf, rv, kpi_objs))
        if key:
            leaf_keys.append(key)

    print("Seeding rollup service ...")
    if leaf_keys:
        deps = [{"serviceid": k, "kpis_depending_on": ["SHKPI-%s" % k]} for k in leaf_keys]
        itsi.upsert("service", PARENT_SERVICE[0], parent_payload(PARENT_SERVICE[1], deps))
    else:
        sys.stderr.write("  skipped rollup - no leaf services were created.\n")

    print("\nDone. Open the IT Service Intelligence app -> Service Analyzer. KPIs need a few "
          "minutes of scheduled runs before they show a value.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
