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

# --- The service tree ------------------------------------------------------
# Each service is a dict:
#   title       - unique service name
#   desc        - description
#   rule        - (field, value) entity rule for the entity inventory view, or None
#   kpis        - list of KPI specs (may be empty for pure branch/rollup nodes)
#   depends_on  - list of child service TITLES whose health rolls up into this one
#
# KPI spec: (title, base_search, threshold_field, aggregate, unit, medium, critical)
#   The base_search is fully scoped (index + host filters), so KPI values do not
#   depend on entity-rule matching. medium/critical are ascending thresholds tuned
#   so normal lab activity reads green.
#
# Services are created in list order: every child appears BEFORE the parent that
# depends on it, so dependency keys resolve.
SERVICES = [
    # ---- shared infrastructure (created first: the app tiers depend on it) --
    # Both containers RUN ON Proxmox, so Web and DB each depend on the
    # hypervisor. Proxmox down -> both tiers degrade -> Directory App -> Berlin.
    {"title": "Proxmox Hypervisor", "desc": "Berlin Proxmox hypervisor - hosts the app containers.",
     "rule": ("role", "hypervisor"), "depends_on": [], "kpis": [
        # Reachability %: active TCP probe of the Proxmox management UI (8006) from
        # the Splunk server (probe_ports.sh) - always present, no agent needed.
        # Hypervisor up -> open=1 (GREEN); host down -> open=0 (RED). This is the
        # GATING health for the tier the containers run on.
        ("Reachability", "index=berlin_web sourcetype=port:probe host=proxmox-berlin | stats count as c latest(open) as o | eval down=if(c>0 AND o=1,0,100)", "down", "max", "%", 50, 90),
        # Enrichment (informational; does not gate - Reachability does):
        ("Proxmox Event Volume", "index=berlin_proxmox", "count", "count", "events", 50000, 200000),
        # SNMP stays on the SNMP dashboard, not a service KPI (optional add-on).
     ]},
    # ---- app tiers (carry KPIs; depend on the hypervisor they run on) ------
    {"title": "Web Service (Berlin)", "desc": "The Berlin web application container.",
     "rule": ("role", "webserver"), "depends_on": ["Proxmox Hypervisor"], "kpis": [
        # Reachability %: an ACTIVE TCP probe of the web port (8080), run FROM the
        # Splunk server every 60s (see infra_monitoring/bin/probe_ports.sh). It
        # emits a PRESENT open=1/0 on every tick - so a down container reads open=0
        # (RED), not a data gap that ITSI paints amber; an idle-but-up app reads
        # open=1 (GREEN); and a crashed app on a still-running container is caught
        # too (port stops answering). latest(open) = the most recent probe result.
        ("Reachability",        "index=berlin_web sourcetype=port:probe host=webapp-berlin | stats count as c latest(open) as o | eval down=if(c>0 AND o=1,0,100)", "down", "max", "%", 50, 90),
        # Enrichment (informational; Reachability gates). Response Latency is an
        # averaged metric -> good candidate for adaptive/ML thresholds (--adaptive).
        ("Response Latency",    "index=berlin_web sourcetype=webapp:probe",              "latency_ms", "avg", "ms", 500, 1500),
        ("HTTP Request Volume", "index=berlin_web sourcetype=webapp:access",             "count", "count", "req",    20000, 80000),
        ("HTTP Errors (5xx)",   "index=berlin_web sourcetype=webapp:access status>=500", "count", "count", "errors", 5,     25),
     ]},
    {"title": "Database Service (Berlin)", "desc": "The Berlin PostgreSQL container.",
     "rule": ("role", "database"), "depends_on": ["Proxmox Hypervisor"], "kpis": [
        # Reachability %: an ACTIVE TCP probe of the Postgres port (5432), run FROM
        # the Splunk server every 60s (probe_ports.sh). Emits a PRESENT open=1/0 on
        # every tick: DB container/process down -> open=0 (decisive RED, not an
        # amber gap); idle-but-healthy DB -> open=1 (GREEN, the fix for "low just
        # because nothing is happening"); Postgres crashed on a live container is
        # caught too. This is a real service check, not an event-volume proxy.
        ("Reachability", "index=berlin_web sourcetype=port:probe host=db-berlin | stats count as c latest(open) as o | eval down=if(c>0 AND o=1,0,100)", "down", "max", "%", 50, 90),
        # Enrichment (informational; Reachability gates):
        ("DB Errors",       "index=berlin_db sourcetype=postgres:log (ERROR OR FATAL)", "count", "count", "errors", 1, 10),
        ("DB Connections",  "index=berlin_db sourcetype=postgres:log \"connection authorized\"", "count", "count", "conns", 5000, 20000),
        ("DB Log Volume",   "index=berlin_db sourcetype=postgres:log", "count", "count", "events", 20000, 80000),
     ]},
    {"title": "Ubuntu Berlin", "desc": "Ubuntu server host (Berlin).",
     "rule": ("host", "ubuntu-berlin"), "depends_on": [], "kpis": [
        # Reachability %: the UF ships linux:metrics every 60s, so recent data =
        # host up. Works on DHCP (no static IP / SSH needed - the host label is
        # fixed). stats count always returns a row, so host down -> up=0 (RED).
        ("Reachability",       "index=berlin_metrics sourcetype=linux:metrics host=ubuntu-berlin | stats count as c | eval down=if(c>0,0,100)", "down", "max", "%", 50, 90),
        # Enrichment (informational; Reachability gates). CPU/mem/disk are averaged
        # metrics -> adaptive/ML thresholds apply with --adaptive.
        ("CPU Utilization",    "index=berlin_metrics sourcetype=linux:metrics host=ubuntu-berlin", "cpu_pct",       "avg", "%", 70, 90),
        ("Memory Utilization", "index=berlin_metrics sourcetype=linux:metrics host=ubuntu-berlin", "mem_used_pct",  "avg", "%", 70, 90),
        ("Disk Usage",         "index=berlin_metrics sourcetype=linux:metrics host=ubuntu-berlin", "disk_used_pct", "avg", "%", 80, 90),
     ]},
    {"title": "Router Berlin", "desc": "Cisco Catalyst 8000v router (Berlin).",
     "rule": ("host", "cat8kv-berlin"), "depends_on": [], "kpis": [
        # Reachability %: active TCP probe of the router's SSH port from the Splunk
        # server - the gating health. Router down -> open=0 (RED), regardless of
        # whether the SSH poll or SNMP happen to have data.
        ("Reachability", "index=berlin_web sourcetype=port:probe host=cat8kv-berlin | stats count as c latest(open) as o | eval down=if(c>0 AND o=1,0,100)", "down", "max", "%", 50, 90),
        # Enrichment (informational; Reachability gates) - SSH poll health:
        ("Poll Errors", "index=berlin_network (\"Connection timed out\" OR \"Connection refused\" OR \"No route to host\" OR \"Unable to negotiate\" OR \"Permission denied\" OR \"Could not resolve\")", "count", "count", "errors", 1, 3),
        ("Poll Volume",  "index=berlin_network", "count", "count", "events", 50000, 200000),
     ]},
    {"title": "Ubuntu London", "desc": "Ubuntu server host (London).",
     "rule": ("host", "ubuntu-london"), "depends_on": [], "kpis": [
        # Reachability via the UF heartbeat (DHCP-safe; see Ubuntu Berlin note).
        ("Reachability",       "index=london_metrics sourcetype=linux:metrics host=ubuntu-london | stats count as c | eval down=if(c>0,0,100)", "down", "max", "%", 50, 90),
        # Enrichment (informational; Reachability gates). Adaptive on CPU/mem/disk.
        ("CPU Utilization",    "index=london_metrics sourcetype=linux:metrics host=ubuntu-london", "cpu_pct",       "avg", "%", 70, 90),
        ("Memory Utilization", "index=london_metrics sourcetype=linux:metrics host=ubuntu-london", "mem_used_pct",  "avg", "%", 70, 90),
        ("Disk Usage",         "index=london_metrics sourcetype=linux:metrics host=ubuntu-london", "disk_used_pct", "avg", "%", 80, 90),
     ]},
    {"title": "Router London", "desc": "Cisco Catalyst 8000v router (London).",
     "rule": ("host", "cat8kv-london"), "depends_on": [], "kpis": [
        # Reachability %: active TCP probe of the router's SSH port (gating).
        ("Reachability", "index=london_web sourcetype=port:probe host=cat8kv-london | stats count as c latest(open) as o | eval down=if(c>0 AND o=1,0,100)", "down", "max", "%", 50, 90),
        # Enrichment (informational; Reachability gates) - SSH poll health:
        ("Poll Errors", "index=london_network (\"Connection timed out\" OR \"Connection refused\" OR \"No route to host\" OR \"Unable to negotiate\" OR \"Permission denied\" OR \"Could not resolve\")", "count", "count", "errors", 1, 3),
        ("Poll Volume",  "index=london_network", "count", "count", "events", 50000, 200000),
     ]},
    {"title": "Splunk Core", "desc": "The Splunk server itself (Location 1).",
     "rule": ("role", "splunk"), "depends_on": [], "kpis": [
        ("CPU Utilization",       "index=loc1_metrics sourcetype=linux:metrics", "cpu_pct",       "avg",   "%",      70, 90),
        ("Memory Utilization",    "index=loc1_metrics sourcetype=linux:metrics", "mem_used_pct",  "avg",   "%",      70, 90),
        ("Disk Usage",            "index=loc1_metrics sourcetype=linux:metrics", "disk_used_pct", "avg",   "%",      80, 90),
        ("Internal Event Volume", "index=_internal",                             "count",         "count", "events", 800000, 2000000),
     ]},
    # ---- mid-level branches ----------------------------------------------
    # The Directory App is the SERVICE the business cares about; it summarises the
    # Web + DB tiers (which in turn depend on the Proxmox hypervisor below them).
    {"title": "Directory App", "desc": "The employee directory service (web tier + database tier).",
     "rule": None, "depends_on": ["Web Service (Berlin)", "Database Service (Berlin)"], "kpis": []},
    {"title": "Berlin Infrastructure", "desc": "Berlin site hosts and network.",
     "rule": None, "depends_on": ["Ubuntu Berlin", "Router Berlin"], "kpis": []},
    {"title": "London Infrastructure", "desc": "London site hosts and network.",
     "rule": None, "depends_on": ["Ubuntu London", "Router London"], "kpis": []},
    # ---- location branches -----------------------------------------------
    {"title": "Berlin", "desc": "Berlin location.",
     "rule": None, "depends_on": ["Directory App", "Berlin Infrastructure"], "kpis": []},
    {"title": "London", "desc": "London location.",
     "rule": None, "depends_on": ["London Infrastructure"], "kpis": []},
    {"title": "Location 1", "desc": "Location 1 (the Splunk core site).",
     "rule": None, "depends_on": ["Splunk Core"], "kpis": []},
    # ---- top of the tree -------------------------------------------------
    {"title": "Global IT Operations", "desc": "Top-level rollup of all locations.",
     "rule": None, "depends_on": ["Berlin", "London", "Location 1"], "kpis": []},
]

# Services from earlier seeder versions that the tree renames/replaces - pruned
# on each run so re-seeding doesn't leave orphans in Service Analyzer.
DEPRECATED_SERVICES = [
    "Hypervisor (Proxmox)",   # -> "Proxmox Hypervisor"
    "Network & Routers",      # -> per-router services
    "Splunk Core (loc1)",     # -> "Splunk Core"
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

    def delete(self, obj, title):
        key = self.find(obj, title)
        if not key:
            return False
        code, _ = self._call("DELETE", "%s/itoa_interface/%s/%s" % (APP_NS, obj, key))
        ok = code in (200, 204)
        print("  %-9s %-8s %s %s" % ("deleted" if ok else "FAILED", obj, title,
                                     "" if ok else ("(HTTP %s)" % code)))
        return ok

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


def up_thresholds(field):
    # "Higher is better" metric (e.g. reachability %): 100 = green, 0 = red.
    # Base severity (values BELOW the first level) is critical; at/above 50 is
    # normal. So 100 -> green, 0 -> red, matching human intuition for "reachable".
    c_v, c_c, c_cl = SEV["critical"]
    n_v, n_c, n_cl = SEV["normal"]
    return {
        "baseSeverityColor": c_c, "baseSeverityColorLight": c_cl,
        "baseSeverityLabel": "critical", "baseSeverityValue": c_v,
        "gaugeMax": 100, "gaugeMin": 0,
        "isMaxStatic": True, "isMinStatic": True,
        "metricField": field, "renderBoundaryMax": 100, "renderBoundaryMin": 0,
        "search": "",
        "thresholdLevels": [
            {"dynamicParam": "", "severityColor": n_c, "severityColorLight": n_cl,
             "severityLabel": "normal", "severityValue": n_v, "thresholdValue": 50},
        ],
    }


def adaptive_thresholds(field):
    # ITSI ML/adaptive thresholding: severities are expressed in standard
    # deviations from the learned mean (dynamicParam = # of stdev) rather than
    # absolute values. High side only (spikes): +2 stdev warn, +3 stdev crit.
    n_v, n_c, n_cl = SEV["normal"]
    levels = []
    for label, stdev in (("medium", 2), ("critical", 3)):
        sv, sc, scl = SEV[label]
        levels.append({"dynamicParam": stdev, "severityColor": sc, "severityColorLight": scl,
                       "severityLabel": label, "severityValue": sv, "thresholdValue": stdev})
    return {
        "baseSeverityColor": n_c, "baseSeverityColorLight": n_cl,
        "baseSeverityLabel": "normal", "baseSeverityValue": n_v,
        "gaugeMax": 100, "gaugeMin": 0,
        "isMaxStatic": False, "isMinStatic": False,
        "metricField": field, "renderBoundaryMax": 100, "renderBoundaryMin": 0,
        "search": "", "thresholdLevels": levels,
    }


def kpi_payload(title, base_search, field, agg, unit, medium, critical, adaptive=False):
    # Adaptive (ML) thresholding only makes sense on continuous averaged metrics
    # (CPU/mem/disk/latency), not on event counts.
    use_adaptive = adaptive and agg == "avg"
    if use_adaptive:
        thr = adaptive_thresholds(field)
    else:
        # Everything (incl. Reachability, now a "down" metric where 0=up/good and
        # 100=down/bad) uses STANDARD ascending thresholds - the same direction as
        # CPU/mem/disk, which ITSI's health score scores correctly (higher=worse).
        thr = thresholds(field, medium, critical)
    # Importance drives the service health score:
    #   11 = GATING - the active Reachability probe. Down => service red,
    #        regardless of the other KPIs (a dead service has 0 traffic, which the
    #        volume KPIs would otherwise read as "green").
    #    5 = normal enrichment - latency, error counts, CPU/mem/disk.
    #    2 = informational - volume/count KPIs (visible, but must not dilute health).
    #    1 = OPTIONAL enrichment - SNMP / traps. SC4SNMP is a add-on agent that may
    #        never be stood up in a given lab; when it is absent these read 0/N/A,
    #        so they MUST NOT be able to drag the tree. Gating health always comes
    #        from the central probes/polls instead.
    tl = title.lower()
    if "snmp" in tl or "trap" in tl:
        importance = 1
    elif "reachab" in tl:
        # GATING (importance 11): reachability defines service health. Down -> the
        # service is critical regardless of the enrichment KPIs; up -> enrichment
        # only adds detail. This is the correct ITSI use of importance 11 now that
        # reachability is a down% metric (higher=worse), so a normal gating KPI no
        # longer inverts the score the way the old up% one did.
        importance = 11
    elif "poll" in tl or "volume" in tl or "connections" in tl:
        # SSH-poll health + volume/count KPIs: informational color, not gating
        # (the central reachability probe is the gate now).
        importance = 2
    else:
        importance = 5
    return {
        "_key": str(uuid.uuid4()),
        "title": title,
        "urgency": 5,
        "importance": importance,
        "search_type": "adhoc",
        "base_search": base_search,
        "threshold_field": field,
        "unit": unit,
        "search_aggregate": agg,
        "entity_statop": agg,
        "aggregate_statop": agg,
        # KPIs are self-scoped (index/host baked into base_search), so don't gate
        # them on the service's entity membership - more reliable across the tree.
        "is_service_entity_filter": False,
        "entity_breakdown_id_fields": "host",
        "entity_id_fields": "host",
        "is_entity_breakdown": False,
        # For the active Reachability probes a data gap means "we lost sight of the
        # service" -> high. But SNMP/trap KPIs are optional (SC4SNMP may be absent),
        # so their gap must be benign or they'd drag the tree when SNMP isn't set up.
        "fill_gaps": "null_value",
        "gap_severity": ("high" if "reachab" in tl else "unknown"),
        # Run every 1 min over a 2-min window so the service tree reacts to a
        # kill in ~1-2 min (ITSI is scheduled-search based - this is its floor;
        # the instant signal is the SNMP trap -> Webex, not the tree colour).
        "alert_period": "1",
        "alert_lag": "30",
        "search_alert_earliest": "2",
        # Backfill so KPI values (and health colours) appear immediately instead
        # of only after the scheduled searches have run for a while.
        # Backfill replays the base search over the last 24h to seed history. For
        # the enrichment KPIs (CPU, counts) that's harmless. But the Reachability
        # search FORCES down=100 (critical) when there's no data - and 24h ago the
        # probes didn't exist, so backfill writes a critical-heavy fake history that
        # drags the health score even when the service is up NOW. So: no backfill on
        # reachability (it's a live probe; health populates within a couple minutes).
        "backfill_enabled": (False if "reachab" in tl else True),
        "backfill_earliest_time": "-24h",
        "time_variate_thresholds": use_adaptive,
        "adaptive_thresholds_is_enabled": use_adaptive,
        "adaptive_thresholding_training_window": "-7d",
        "kpi_threshold_template_id": "",
        "kpi_base_search": "",
        "source": "service_kpi",
        "aggregate_thresholds": thr,
        "entity_thresholds": thr,
    }


def service_payload(desc, rule, kpis, depends_on_keys):
    # rule is (field, value) or None; field "host" matches the entity identifier
    # (alias), "site"/"role" match informational fields. depends_on_keys is a list
    # of child service _keys whose health scores roll up into this service.
    svc = {
        "description": desc,
        "enabled": 1,
        "entity_rules": [],
        "kpis": kpis,
    }
    if rule:
        field, value = rule
        field_type = "alias" if field == "host" else "info"
        svc["entity_rules"] = [{
            "rule_condition": "AND",
            "rule_items": [{"field": field, "field_type": field_type,
                            "rule_type": "matches", "value": value}],
        }]
    if depends_on_keys:
        svc["services_depends_on"] = [
            {"serviceid": k, "kpis_depending_on": ["SHKPI-%s" % k]} for k in depends_on_keys
        ]
    return svc


def main():
    ap = argparse.ArgumentParser(description="Seed ITE-W / ITSI with demo entities, services and KPIs.")
    ap.add_argument("--host", default=os.environ.get("SPLUNK_MGMT", "https://localhost:8089"),
                    help="Splunk management URI (default https://localhost:8089)")
    ap.add_argument("--user", default=os.environ.get("SPLUNK_ADMIN_USER", "admin"))
    ap.add_argument("--password", default=os.environ.get("SPLUNK_ADMIN_PASSWORD", "C1sco12345"))
    ap.add_argument("--verbose", action="store_true", help="print server error bodies")
    ap.add_argument("--dry-run", action="store_true", help="print what would be created, call nothing")
    ap.add_argument("--adaptive", action="store_true",
                    help="enable ITSI adaptive/ML thresholding on CPU/mem/disk/latency KPIs "
                         "(premium; needs an ITSI license). Static thresholds otherwise.")
    ap.add_argument("--wipe", action="store_true",
                    help="DELETE every existing ITSI service (and its KPIs/scheduled searches) "
                         "before seeding, for a clean slate. Use after repeated re-seeds have "
                         "left orphaned KPIs that corrupt the health score.")
    args = ap.parse_args()

    if args.dry_run:
        print("DRY RUN - would create %d entities and %d services (tree):"
              % (len(ENTITIES), len(SERVICES)))
        for h, s, role, d in ENTITIES:
            print("  entity  %-16s site=%-7s role=%s" % (h, s, role))
        for svc in SERVICES:
            kpis = ", ".join(k[0] for k in svc["kpis"]) or "-"
            deps = ", ".join(svc["depends_on"]) or "-"
            print("  service %-24s KPIs: %-42s depends: %s" % (svc["title"], kpis, deps))
        return 0

    itsi = ITSI(args.host, args.user, args.password, verbose=args.verbose)

    # Preflight: is ITSI reachable?
    code, _ = itsi._call("GET", "%s/itoa_interface/service" % APP_NS, params={"count": "1"})
    if code != 200:
        sys.stderr.write("ERROR: ITSI REST not reachable (HTTP %s at %s). Is ITE-W installed and "
                         "Splunk restarted? Are the admin creds correct?\n" % (code, args.host))
        return 1

    if args.wipe:
        print("Wiping ALL existing ITSI services (clean slate) ...")
        code, res = itsi._call("GET", "%s/itoa_interface/service" % APP_NS,
                               params={"fields": "_key,title"})
        if code == 200 and isinstance(res, list):
            for s in res:
                k = s.get("_key"); t = s.get("title", "?")
                dc, _ = itsi._call("DELETE", "%s/itoa_interface/service/%s" % (APP_NS, k))
                print("  %s service %s" % ("deleted" if dc in (200, 204) else "FAILED", t))
            print("  wiped %d services. Recreating clean ..." % len(res))
        else:
            sys.stderr.write("  WARN: could not list services to wipe (HTTP %s)\n" % code)

    print("Seeding ITSI entities ...")
    for host, site, role, desc in ENTITIES:
        itsi.upsert("entity", host, entity_payload(host, site, role, desc))

    print("Seeding ITSI service tree ...")
    keys = {}   # title -> _key, filled as we go (children first)
    for svc in SERVICES:
        missing = [d for d in svc["depends_on"] if d not in keys]
        if missing:
            sys.stderr.write("  WARN %s: unresolved dependencies %s (order?) - creating without them.\n"
                             % (svc["title"], missing))
        dep_keys = [keys[d] for d in svc["depends_on"] if d in keys]
        kpi_objs = [kpi_payload(*k, adaptive=args.adaptive) for k in svc["kpis"]]
        key = itsi.upsert("service", svc["title"],
                          service_payload(svc["desc"], svc["rule"], kpi_objs, dep_keys))
        if key:
            keys[svc["title"]] = key

    # Prune services from older seeder versions (renamed/replaced by the tree)
    # so Service Analyzer doesn't keep the orphans. Done last, after the new tree
    # (with rewritten dependencies) no longer references them.
    print("Pruning deprecated services ...")
    for title in DEPRECATED_SERVICES:
        itsi.delete("service", title)

    print("\nDone. Open IT Service Intelligence -> Service Analyzer -> Tree view to see the "
          "location hierarchy. KPIs need a few minutes of scheduled runs (backfill seeds recent "
          "values) before health colours settle.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
