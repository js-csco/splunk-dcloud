#!/usr/bin/env python3
# ===========================================================================
# seed_glass_table.py - create a native ITSI Glass Table (premium) via API.
#
# ITSI 5.0 Glass Tables use a Dashboard-Studio-style "definition" posted to the
# itoa_interface/glass_table endpoint. This builds one laid out as the lab's
# location tree (Berlin / London / Location 1) with colour-coded single-value
# tiles, each backed by the same live signals as the ITSI KPIs - so it needs no
# service _keys and renders regardless of KPI state.
#
# RUN (after ITSI is installed + licensed):
#   sudo -u splunk /opt/splunk/bin/splunk cmd python3 \
#     /opt/dcloud-splunk/itsi/seed_glass_table.py --user admin --password C1sco12345 --verbose
#
# BEST-EFFORT: the Glass Table / Dashboard Studio schema shifts between ITSI
# versions. Run with --verbose; if the server rejects the definition, paste the
# error and the layout/encoding below can be tuned. Native Glass Tables require
# an ITSI license (premium); on free ITE-W use the SimpleXML "Service Glass
# Table" dashboard instead.
# ===========================================================================
import argparse
import base64
import json
import os
import ssl
import sys
from urllib import request, parse, error

APP_NS = "servicesNS/nobody/itsi"
SEC_GRP = "default_itsi_security_group"
GT_TITLE = "dCloud Service Tree"

GREEN, AMBER, RED = "#53A051", "#F8BE34", "#D93F3C"
REACH = [{"to": 50, "value": GREEN}, {"from": 50, "value": RED}]          # 0 up / 100 down
CPU   = [{"to": 70, "value": GREEN}, {"from": 70, "to": 90, "value": AMBER}, {"from": 90, "value": RED}]
FRESH = [{"to": 1, "value": GREEN}, {"from": 1, "to": 80, "value": AMBER}, {"from": 80, "value": RED}]

E = "earliest=-15m"

# tiles: (id, title, x, y, query, colorConfig, unit)
TILES = [
    ("global", "Global IT Operations", 500, 20,
     "index=berlin_web sourcetype=port:probe port=8080 %s | stats latest(open) as w "
     "| appendcols [ search index=berlin_web sourcetype=port:probe port=5432 %s | stats latest(open) as d ] "
     "| eval v=if(w==1 AND d==1,0,100) | fields v" % (E, E), REACH, ""),
    # Berlin column
    ("proxmox", "Proxmox Hypervisor", 40, 190,
     "index=berlin_proxmox %s | stats count as c | eval v=if(c=0,50,0) | fields v" % E, FRESH, ""),
    ("web", "Web Service", 40, 280,
     "index=berlin_web sourcetype=port:probe port=8080 %s | stats latest(open) as o "
     "| eval v=case(o==1,0,o==0,100,1==1,0) | fields v" % E, REACH, ""),
    ("db", "Database Service", 40, 370,
     "index=berlin_web sourcetype=port:probe port=5432 %s | stats latest(open) as o "
     "| eval v=case(o==1,0,o==0,100,1==1,0) | fields v" % E, REACH, ""),
    ("ubuntu_ber", "Ubuntu Berlin", 40, 460,
     "index=berlin_metrics sourcetype=linux:metrics host=ubuntu-berlin %s | stats latest(cpu_pct) as v | fields v" % E, CPU, "%"),
    ("router_ber", "Router Berlin", 40, 550,
     "index=berlin_network (\"Connection timed out\" OR \"Connection refused\" OR \"No route to host\" OR \"Unable to negotiate\" OR \"Permission denied\") %s "
     "| stats count as c | eval v=if(c=0,0,100) | fields v" % E, REACH, ""),
    # London column
    ("ubuntu_lon", "Ubuntu London", 500, 190,
     "index=london_metrics sourcetype=linux:metrics host=ubuntu-london %s | stats latest(cpu_pct) as v | fields v" % E, CPU, "%"),
    ("router_lon", "Router London", 500, 280,
     "index=london_network (\"Connection timed out\" OR \"Connection refused\" OR \"No route to host\" OR \"Unable to negotiate\" OR \"Permission denied\") %s "
     "| stats count as c | eval v=if(c=0,0,100) | fields v" % E, REACH, ""),
    # Location 1 column
    ("splunk_core", "Splunk Core", 900, 190,
     "index=loc1_metrics sourcetype=linux:metrics %s | stats latest(cpu_pct) as v | fields v" % E, CPU, "%"),
]

# section labels: (id, text, x, y)
LABELS = [
    ("lbl_berlin", "# Berlin", 40, 150),
    ("lbl_london", "# London", 500, 150),
    ("lbl_loc1",   "# Location 1", 900, 150),
]


def build_definition():
    data_sources, visualizations, structure = {}, {}, []

    for tid, text, x, y in LABELS:
        vid = "viz_%s" % tid
        visualizations[vid] = {"type": "splunk.markdown", "options": {"markdown": text}}
        structure.append({"item": vid, "type": "block", "position": {"x": x, "y": y, "w": 200, "h": 30}})

    for tid, title, x, y, query, colorcfg, unit in TILES:
        dsid, vid = "ds_%s" % tid, "viz_%s" % tid
        data_sources[dsid] = {
            "type": "ds.search",
            "options": {"query": query, "queryParameters": {"earliest": "-15m", "latest": "now"}},
            "name": title,
        }
        opts = {
            "majorValue": "> primary | seriesByName('v') | lastPoint()",
            "backgroundColor": "> majorValue | rangeValue(colorCfg)",
            "sparklineDisplay": "off",
            "trendDisplay": "off",
        }
        if unit:
            opts["unit"] = unit
        visualizations[vid] = {
            "type": "splunk.singlevalue",
            "title": title,
            "dataSources": {"primary": dsid},
            "options": opts,
            "context": {"colorCfg": colorcfg},
        }
        structure.append({"item": vid, "type": "block", "position": {"x": x, "y": y, "w": 320, "h": 70}})

    return {
        "title": GT_TITLE,
        "description": "dCloud service health, laid out as the location tree.",
        "dataSources": data_sources,
        "visualizations": visualizations,
        "inputs": {},
        "layout": {
            "type": "absolute",
            "options": {"width": 1240, "height": 660, "backgroundColor": "#0B0E12"},
            "structure": structure,
        },
        "defaults": {"visualizations": {"global": {"showLastUpdated": True}}},
    }


class ITSI:
    def __init__(self, base, user, password, verbose=False):
        self.base, self.verbose = base.rstrip("/"), verbose
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        self.auth = "Basic " + base64.b64encode(("%s:%s" % (user, password)).encode()).decode()

    def call(self, method, path, body=None, params=None):
        url = "%s/%s" % (self.base, path.lstrip("/"))
        if params:
            url += "?" + parse.urlencode(params)
        data = parse.urlencode(body).encode() if body else None
        hdrs = {"Authorization": self.auth}
        if data:
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            with request.urlopen(request.Request(url, data=data, headers=hdrs, method=method),
                                 timeout=30, context=self.ctx) as r:
                raw = r.read().decode()
                return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
        except error.HTTPError as exc:
            detail = exc.read().decode()[:800]
            if self.verbose:
                sys.stderr.write("  HTTP %s %s -> %s\n" % (exc.code, path, detail))
            return exc.code, detail


def main():
    ap = argparse.ArgumentParser(description="Create an ITSI Glass Table for the dCloud service tree.")
    ap.add_argument("--host", default=os.environ.get("SPLUNK_MGMT", "https://localhost:8089"))
    ap.add_argument("--user", default=os.environ.get("SPLUNK_ADMIN_USER", "admin"))
    ap.add_argument("--password", default=os.environ.get("SPLUNK_ADMIN_PASSWORD", "C1sco12345"))
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--string-definition", action="store_true",
                    help="send 'definition' as a JSON string instead of an object (try this if "
                         "the object form is rejected)")
    args = ap.parse_args()

    itsi = ITSI(args.host, args.user, args.password, verbose=args.verbose)
    definition = build_definition()
    payload = {
        "title": GT_TITLE,
        "description": definition["description"],
        "sec_grp": SEC_GRP,
        "definition": json.dumps(definition) if args.string_definition else definition,
    }

    # idempotent: update if a glass table with this title already exists
    code, res = itsi.call("GET", "%s/itoa_interface/glass_table" % APP_NS,
                          params={"filter": json.dumps({"title": GT_TITLE}), "fields": "_key,title"})
    if code != 200:
        sys.stderr.write("ERROR: cannot reach itoa_interface/glass_table (HTTP %s). Is ITSI "
                         "installed, licensed, and are the creds right?\n" % code)
        return 1
    key = res[0]["_key"] if isinstance(res, list) and res else None

    if key:
        code, res = itsi.call("POST", "%s/itoa_interface/glass_table/%s" % (APP_NS, key),
                              body={"data": json.dumps(dict(payload, _key=key))})
        action = "updated"
    else:
        code, res = itsi.call("POST", "%s/itoa_interface/glass_table" % APP_NS,
                              body={"data": json.dumps(payload)})
        action = "created"

    if code in (200, 201):
        print("Glass table %s: '%s'. Open ITSI -> Glass Tables to view it." % (action, GT_TITLE))
        return 0
    sys.stderr.write("FAILED to %s glass table (HTTP %s). Re-run with --verbose for the error "
                     "body; if it mentions the definition, try --string-definition.\n" % (action, code))
    return 1


if __name__ == "__main__":
    sys.exit(main())
