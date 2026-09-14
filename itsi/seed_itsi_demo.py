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
#   sudo -u splunk /opt/splunk/bin/python3 \
#     /opt/dcloud-splunk/itsi/seed_itsi_demo.py --user admin --password C1sco12345
# (or from anywhere with network to the box: --host https://198.18.1.124:8089)
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
# Hosts to register as ITSI entities. (host, site, description)
ENTITIES = [
    ("desktop-london",     "london", "Linux desktop client (London)"),
    ("ubuntu-london",      "london", "Ubuntu server (London)"),
    ("proxmox-berlin",     "berlin", "Proxmox hypervisor (Berlin)"),
    ("webapp-berlin",      "berlin", "Web-app container (Berlin)"),
    ("db-berlin",          "berlin", "PostgreSQL container (Berlin)"),
    ("ubuntu-berlin",      "berlin", "Ubuntu server (Berlin)"),
    ("splunk",             "loc1",   "Splunk server (Location 1)"),
]

# Services: (title, description, site the entity rule matches, [kpi specs])
# Each KPI spec: (title, base_search, threshold_field, aggregate, unit, medium, critical)
SERVICES = [
    ("London Infrastructure", "Health of the London site hosts.", "london", [
        ("CPU Utilization",    "index=london_metrics sourcetype=linux:metrics", "cpu_pct",      "avg", "%", 70, 90),
        ("Memory Utilization", "index=london_metrics sourcetype=linux:metrics", "mem_used_pct", "avg", "%", 70, 90),
    ]),
    ("Berlin Infrastructure", "Health of the Berlin site hosts.", "berlin", [
        ("CPU Utilization",    "index=berlin_metrics sourcetype=linux:metrics", "cpu_pct",      "avg", "%", 70, 90),
        ("Memory Utilization", "index=berlin_metrics sourcetype=linux:metrics", "mem_used_pct", "avg", "%", 70, 90),
    ]),
    ("Web Service (Berlin)", "The Berlin web application service.", "berlin", [
        ("HTTP Request Volume", "index=berlin_web sourcetype=webapp:access", "count", "count", "req", 0, 0),
    ]),
]

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


def entity_payload(host, site, desc):
    # ITSI entity: identifier/informational fields must ALSO be present as
    # top-level list keys (host, site).
    return {
        "description": desc,
        "identifier": {"fields": ["host"], "values": [host]},
        "informational": {"fields": ["site"], "values": [site]},
        "host": [host],
        "site": [site],
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
    return {
        "baseSeverityColor": n_c, "baseSeverityColorLight": n_cl,
        "baseSeverityLabel": "normal", "baseSeverityValue": n_v,
        "gaugeMax": max(100, (critical or 100) * 1.2), "gaugeMin": 0,
        "isMaxStatic": False, "isMinStatic": True,
        "metricField": field, "renderBoundaryMax": 100, "renderBoundaryMin": 0,
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
        "time_variate_thresholds": False,
        "adaptive_thresholds_is_enabled": False,
        "adaptive_thresholding_training_window": "-7d",
        "kpi_threshold_template_id": "",
        "kpi_base_search": "",
        "source": "service_kpi",
        "aggregate_thresholds": thresholds(field, medium, critical),
        "entity_thresholds": thresholds(field, medium, critical),
    }


def service_payload(desc, site, kpis):
    return {
        "description": desc,
        "enabled": 1,
        "entity_rules": [{
            "rule_condition": "AND",
            "rule_items": [{"field": "site", "field_type": "info",
                            "rule_type": "matches", "value": site}],
        }],
        "kpis": kpis,
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
        print("DRY RUN - would create %d entities and %d services:" % (len(ENTITIES), len(SERVICES)))
        for h, s, d in ENTITIES:
            print("  entity  %-16s site=%s" % (h, s))
        for t, d, s, kpis in SERVICES:
            print("  service %-24s site=%s  KPIs: %s" % (t, s, ", ".join(k[0] for k in kpis)))
        return 0

    itsi = ITSI(args.host, args.user, args.password, verbose=args.verbose)

    # Preflight: is ITSI reachable?
    code, _ = itsi._call("GET", "%s/itoa_interface/service" % APP_NS, params={"count": "1"})
    if code != 200:
        sys.stderr.write("ERROR: ITSI REST not reachable (HTTP %s at %s). Is ITE-W installed and "
                         "Splunk restarted? Are the admin creds correct?\n" % (code, args.host))
        return 1

    print("Seeding ITSI entities ...")
    for host, site, desc in ENTITIES:
        itsi.upsert("entity", host, entity_payload(host, site, desc))

    print("Seeding ITSI services + KPIs ...")
    for title, desc, site, kpis in SERVICES:
        kpi_objs = [kpi_payload(*k) for k in kpis]
        itsi.upsert("service", title, service_payload(desc, site, kpi_objs))

    print("\nDone. Open the IT Service Intelligence app -> Service Analyzer. KPIs need a few "
          "minutes of scheduled runs before they show a value.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
