#!/usr/bin/env python3
"""Generate the Customer Example app's Dashboard Studio views.

Source of truth for splunk/apps/customer_example/default/data/ui/views/*.xml -
edit here, not the XML. Every panel is backed by realistic sample data for a
fictional hospital ("Spital Aarwald"). The data is embedded in each search via
`| makeresults format=csv`, and timestamps are computed at search time, so the
dashboards need no data onboarding and always look current (daytime peaks land
on daytime, weekends on weekends).

    python3 scripts/build_customer_example.py
    python3 scripts/build_customer_example.py --preview DIR
        # also write DIR/<view>.json with the same numbers as ds.test data,
        # for rendering outside Splunk with the Dashboard Studio framework
"""
import argparse
import datetime as dt
import json
import math
import os
import random
import re

APP = "customer_example"
HOSPITAL = "Spital Aarwald"
VIEWS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                         "splunk", "apps", APP, "default", "data", "ui", "views")

# ---------------------------------------------------------------- design system
PAGE = "#0E1116"
CARD = "#161B22"
CARD_STROKE = "#262C36"
CHIP = "#1C2530"
CHIP_STROKE = "#2D3A48"
INK = "#E6EDF3"
INK_2 = "#9AA4B2"
MUTED = "#6E7681"
ACCENT = "#16A3B0"

# Categorical order validated on the dark card surface (dataviz validator).
BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET, RED = (
    "#3987E5", "#D95926", "#199E70", "#C98500", "#D55181", "#008300", "#9085E9", "#E66767")
CAT = [BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET, RED]
# Status colors are reserved for state, never used as a series color.
GOOD, WARN, SERIOUS, CRIT = "#0CA30C", "#FAB219", "#EC835A", "#D03B3B"
# Tinted cells for status in tables: (background, text).
TINT = {GOOD: ("#12301B", "#56D364"), WARN: ("#3A2E12", "#E3B341"),
        SERIOUS: ("#3A2518", "#F0A070"), CRIT: ("#3F1D20", "#FF8A80"), MUTED: ("#1F242C", "#9AA4B2")}

W = 1440
M = 24
G = 16
INNER = W - 2 * M
LEFT = 904                     # main column width
RIGHT = INNER - LEFT - G       # side column width

DAY, WEEK, HOUR, MIN5, MIN = 86400, 604800, 3600, 300, 60


# ---------------------------------------------------------------- data
class Data:
    """Sample rows that render either as SPL (makeresults) or as ds.test data.

    kind=None     plain table rows
    kind="ago"    first value = buckets ago (0 = current bucket)
    kind="clock"  first two values = (cycles ago, slot in cycle); pins the row
                  to a wall-clock slot so daily/weekly rhythms line up with the
                  viewer's actual time. `window` limits to the last N seconds.
    rel: {field: seconds} - that column holds "units ago" and is rendered as
         a weekday + time label, computed at search time.
    ramps: [(field, amount)] - add up to `amount`, rising linearly from 0 at the
           window start to full at now (a trend that always ends "now").
    bumps: [(field, from_h, to_h, amount)] - add `amount` to buckets between
           from_h and to_h hours ago (an incident at a fixed distance from now).
    """

    def __init__(self, fields, rows, kind=None, unit=None, cycle=None, window=None, rel=None,
                 ramps=None, bumps=None):
        self.fields, self.rows, self.kind = fields, rows, kind
        self.unit, self.cycle, self.window, self.rel = unit, cycle, window, rel or {}
        self.ramps, self.bumps = ramps or [], bumps or []

    def _csv_header(self):
        if self.kind == "ago":
            return ["ago"] + self.fields[1:]
        if self.kind == "clock":
            return ["cyc", "slot"] + self.fields[1:]
        return list(self.fields)

    def spl(self):
        lines = [",".join(self._csv_header())] + [",".join(_csv(v) for v in r) for r in self.rows]
        q = '| makeresults format=csv data="' + "\n".join(lines) + '"'
        if self.kind == "ago":
            snap = "@d" if self.unit >= DAY else ("@h" if self.unit == HOUR else "@m")
            q += '\n| eval _time=relative_time(now(), "%s") - ago*%d' % (snap, self.unit)
        elif self.kind == "clock":
            snap, length = ("@d", DAY) if self.cycle == DAY else ("@w0", WEEK)
            q += '\n| eval _time=relative_time(now(), "%s") + slot*%d - cyc*%d' % (snap, self.unit, length)
            q += "\n| where _time<=now() AND _time>now()-%d" % self.window
        if self.kind:
            q += "\n| sort 0 _time"
        for f, amount in self.ramps:
            q += "\n| eval \"%s\"=round('%s' + %s*(1-(now()-_time)/%d), 1)" % (f, f, amount, self.window)
        for f, a, b, amount in self.bumps:
            q += "\n| eval \"%s\"='%s' + if(now()-_time>=%d AND now()-_time<%d, %s, 0)" % (f, f, a * HOUR, b * HOUR, amount)
        for f, unit in self.rel.items():
            q += '\n| eval "%s"=strftime(relative_time(now(), "@m") - \'%s\'*%d, "%%a %%H:%%M")' % (f, f, unit)
        q += "\n| table " + " ".join(f if re.fullmatch(r"\w+", f) else '"%s"' % f for f in self.fields)
        return q

    def test_data(self, now):
        out = []
        for r in self.rows:
            r = list(r)
            if self.kind == "ago":
                t = _floor(now, self.unit) - dt.timedelta(seconds=r[0] * self.unit)
                r = [t] + r[1:]
            elif self.kind == "clock":
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                length = DAY
                if self.cycle == WEEK:
                    start -= dt.timedelta(days=(start.weekday() + 1) % 7)   # back to Sunday
                    length = WEEK
                t = start + dt.timedelta(seconds=r[1] * self.unit - r[0] * length)
                if not (t <= now and t > now - dt.timedelta(seconds=self.window)):
                    continue
                r = [t] + r[2:]
            if self.kind:
                age = (now - r[0]).total_seconds()
                for f, amount in self.ramps:
                    i = self.fields.index(f)
                    r[i] = round(r[i] + amount * (1 - age / self.window), 1)
                for f, a, b, amount in self.bumps:
                    if a * HOUR <= age < b * HOUR:
                        r[self.fields.index(f)] += amount
            for f, unit in self.rel.items():
                i = self.fields.index(f)
                r[i] = (now - dt.timedelta(seconds=r[i] * unit)).strftime("%a %H:%M")
            out.append(r)
        if self.kind:
            out.sort(key=lambda x: x[0])
            for r in out:
                r[0] = r[0].strftime("%Y-%m-%dT%H:%M:%S")
        cols = [[r[i] for r in out] for i in range(len(self.fields))]
        return {"fields": [{"name": f} for f in self.fields], "columns": cols}


def _floor(t, unit):
    if unit >= DAY:
        return t.replace(hour=0, minute=0, second=0, microsecond=0)
    if unit == HOUR:
        return t.replace(minute=0, second=0, microsecond=0)
    return t.replace(second=0, microsecond=0)


def _csv(v):
    s = str(v)
    assert "," not in s and '"' not in s and "\n" not in s, s
    return s


def _r(v):
    return round(v, 2) if isinstance(v, float) else v


def ago(n, unit, names, fn):
    """n buckets ending now; fn(k) with k=0 the oldest bucket."""
    return Data(["_time"] + names, [[n - 1 - k] + [_r(v) for v in fn(k)] for k in range(n)],
                kind="ago", unit=unit)


def clock_hours(days, names, fn, ramps=None, bumps=None):
    """Hourly buckets over the last `days` days, pinned to the wall clock.
    fn(weekday, hour) - weekday 0=Sunday. Trends/incidents go in ramps/bumps."""
    cycles = math.ceil(days / 7) + 1
    rows = [[c, s] + [_r(v) for v in fn(s // 24, s % 24)] for c in range(cycles) for s in range(168)]
    return Data(["_time"] + names, rows, kind="clock", unit=HOUR, cycle=WEEK, window=days * DAY,
                ramps=ramps, bumps=bumps)


def clock_days(days, names, fn, ramps=None):
    """Daily buckets over the last `days` days, pinned to real weekdays.
    fn(weekday) - weekday 0=Sunday."""
    cycles = math.ceil(days / 7) + 1
    rows = [[c, s] + [_r(v) for v in fn(s)] for c in range(cycles) for s in range(7)]
    return Data(["_time"] + names, rows, kind="clock", unit=DAY, cycle=WEEK, window=days * DAY, ramps=ramps)


def tbl(fields, rows, rel=None):
    return Data(fields, rows, rel=rel)


def diurnal(hour, low, high, peak=14.0):
    x = math.cos((hour - peak) / 24 * 2 * math.pi)
    return low + (high - low) * (x + 1) / 2


# ---------------------------------------------------------------- dashboard builder
class Dash:
    def __init__(self, view, title, description):
        self.view, self.title, self.description = view, title, description
        self.viz, self.ds, self.struct = {}, {}, []
        self.n = 0
        self.height = 0

    def _id(self, p):
        self.n += 1
        return "%s_%03d" % (p, self.n)

    def _place(self, vid, x, y, w, h):
        self.struct.append({"item": vid, "type": "block", "position": {"x": x, "y": y, "w": w, "h": h}})
        self.height = max(self.height, y + h)

    def rect(self, x, y, w, h, fill=CARD, stroke=CARD_STROKE, rx=12):
        vid = self._id("bg")
        self.viz[vid] = {"type": "splunk.rectangle",
                         "options": {"fillColor": fill, "strokeColor": stroke, "strokeWidth": 1, "rx": rx}}
        self._place(vid, x, y, w, h)

    def md(self, x, y, w, h, text, size=None, color=INK):
        vid = self._id("md")
        opts = {"markdown": text, "fontColor": color}
        if isinstance(size, int):
            opts.update(fontSize="custom", customFontSize=size)
        elif size:
            opts["fontSize"] = size
        self.viz[vid] = {"type": "splunk.markdown", "options": opts}
        self._place(vid, x, y, w, h)

    def card(self, x, y, w, h, vtype, data, title=None, desc=None, options=None, context=None):
        self.rect(x, y, w, h)
        vid = self._id("viz")
        opts = {"backgroundColor": CARD}
        opts.update(options or {})
        did = self._id("ds")
        self.ds[did] = data
        v = {"type": vtype, "options": opts, "dataSources": {"primary": did}}
        if title:
            v["title"] = title
        if desc:
            v["description"] = desc
        if context:
            v["context"] = context
        self.viz[vid] = v
        self._place(vid, x + 6, y + 6, w - 12, h - 12)

    # ------------------------------------------------------------ blocks
    def header(self, number, title, subtitle, quote=None):
        y, h = 20, 116
        self.rect(M, y, INNER, h)
        self.rect(M, y, 6, h, fill=ACCENT, stroke=ACCENT, rx=3)
        tag = ("USE CASE %d OF 9  ·  %s  ·  SAMPLE DATA" % (number, HOSPITAL.upper())) if number \
            else "%s  ·  SPLUNK USE CASES  ·  SAMPLE DATA" % HOSPITAL.upper()
        self.md(M + 28, y + 14, 820, 22, tag, size=12, color=ACCENT)
        self.md(M + 28, y + 36, 880, 44, "# " + title, color=INK)
        self.md(M + 28, y + 80, 880, 28, subtitle, size=15, color=INK_2)
        if quote:
            self.rect(W - M - 488, y + 16, 464, h - 32, fill="#11161D", stroke=CARD_STROKE, rx=8)
            self.md(W - M - 476, y + 22, 444, 20, "WHAT WE HEARD", size=11, color=MUTED)
            self.md(W - M - 476, y + 42, 444, h - 60, "*“" + quote + "”*", size=13, color=INK_2)
        return y + h + G

    def kpis(self, y, items, h=164):
        n = len(items)
        w = (INNER - (n - 1) * G) // n
        for i, k in enumerate(items):
            self.kpis_at(M + i * (w + G), y, w, h, k)
        return y + h + G

    def kpis_at(self, x, y, w, h, k):
        spark = k.get("spark", ACCENT)
        opts = {"majorColor": INK, "numberPrecision": k.get("precision", 0),
                "shouldUseThousandSeparators": True, "majorFontSize": 42, "trendFontSize": 15,
                "trendDisplay": k.get("trend", "absolute")}
        if spark:
            opts.update(sparklineDisplay="below", showSparklineAreaGraph=True,
                        sparklineStrokeColor=spark, sparklineAreaColor=spark)
        else:
            opts["sparklineDisplay"] = "off"
        ctx = {}
        if k.get("unit"):
            opts["unit"] = k["unit"]
            opts["unitPosition"] = k.get("unit_pos", "after")
        if k.get("under"):
            opts.update(underLabel=k["under"], underLabelColor=INK_2, underLabelFontSize=13)
        good = k.get("good")
        if good:
            up, down = (GOOD, CRIT) if good == "up" else (CRIT, GOOD)
            opts["trendColor"] = "> trendValue | rangeValue(trendColorConfig)"
            ctx["trendColorConfig"] = [{"to": -0.0001, "value": down},
                                       {"from": -0.0001, "to": 0.0001, "value": INK_2},
                                       {"from": 0.0001, "value": up}]
        if k.get("thresholds"):
            opts["majorColor"] = "> majorValue | rangeValue(majorColorConfig)"
            ctx["majorColorConfig"] = k["thresholds"]
        self.card(x, y, w, h, "splunk.singlevalue", k["data"], title=k["title"], options=opts,
                  context=ctx or None)

    def chips(self, y, sources):
        rows, x = [[]], M + 20
        for s in sources:
            cw = int(len(s) * 7.4) + 34
            if x + cw > W - M - 20:
                rows.append([])
                x = M + 20
            rows[-1].append((s, x, cw))
            x += cw + 10
        h = 60 + 44 * len(rows)
        self.rect(M, y, INNER, h)
        self.md(M + 20, y + 16, 700, 22, "DATA SOURCES NEEDED IN SPLUNK", size=12, color=ACCENT)
        for i, r in enumerate(rows):
            cy = y + 50 + i * 44
            for s, cx, cw in r:
                self.rect(cx, cy, cw, 34, fill=CHIP, stroke=CHIP_STROKE, rx=17)
                self.md(cx + 16, cy + 7, cw - 18, 22, s, size=13, color=INK)
        return y + h + G

    # ------------------------------------------------------------ output
    def definition(self, mode, now):
        ds = {}
        for did, d in self.ds.items():
            if mode == "splunk":
                ds[did] = {"type": "ds.search", "name": did,
                           "options": {"query": d.spl(),
                                       "queryParameters": {"earliest": "-15m", "latest": "now"}}}
            else:
                ds[did] = {"type": "ds.test", "name": did, "options": {"data": d.test_data(now)}}
        return {
            "title": self.title,
            "description": self.description,
            "inputs": {},
            "defaults": {},
            "visualizations": self.viz,
            "dataSources": ds,
            "layout": {
                "globalInputs": [],
                "tabs": {"items": [{"layoutId": "layout_1", "label": "Dashboard"}]},
                "layoutDefinitions": {"layout_1": {
                    "type": "absolute",
                    "options": {"width": W, "height": self.height + 24, "display": "auto-scale",
                                "backgroundColor": PAGE},
                    "structure": self.struct}},
            },
        }

    def xml(self, now):
        body = json.dumps(self.definition("splunk", now), indent=2, ensure_ascii=False)
        assert "]]>" not in body
        return ('<dashboard version="2" theme="dark">\n'
                "  <label>%s</label>\n"
                "  <description>%s</description>\n"
                "  <definition><![CDATA[\n%s\n  ]]></definition>\n"
                '  <meta type="hiddenElements"><![CDATA[\n'
                '{"hideEdit": false, "hideOpenInSearch": false, "hideExport": false}\n'
                "  ]]></meta>\n"
                "</dashboard>\n") % (_esc(self.title), _esc(self.description), body)


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- option presets
def line_opts(colors, ytitle=None, legend="bottom", **extra):
    o = {"legendDisplay": legend, "xAxisTitleVisibility": "hide", "nullValueDisplay": "connect",
         "lineWidth": 2, "seriesColors": colors}
    if ytitle:
        o["yAxisTitleText"] = ytitle
    else:
        o["yAxisTitleVisibility"] = "hide"
    o.update(extra)
    return o


def column_opts(colors, stacked=True, ytitle=None, legend="bottom", **extra):
    o = {"legendDisplay": legend, "xAxisTitleVisibility": "hide", "seriesColors": colors}
    if stacked:
        o["stackMode"] = "stacked"
    if ytitle:
        o["yAxisTitleText"] = ytitle
    else:
        o["yAxisTitleVisibility"] = "hide"
    o.update(extra)
    return o


def bar_opts(color, **extra):
    o = {"legendDisplay": "off", "xAxisTitleVisibility": "hide", "yAxisTitleVisibility": "hide",
         "seriesColors": [color], "dataValuesDisplay": "all"}
    o.update(extra)
    return o


def donut_opts(colors=None):
    return {"showDonutHole": True, "labelDisplay": "valuesAndPercentage", "legendDisplay": "off",
            "seriesColors": colors or CAT}


def table_opts(status=None, extra_ctx=None, font="small", widths=None):
    """status: {field: {value: STATUS_COLOR}} tints those cells; widths: {field: px}."""
    opts = {"headerVisibility": "fixed", "fontSize": font, "columnFormat": {}}
    ctx = {}
    for field, px in (widths or {}).items():
        opts["columnFormat"].setdefault(field, {})["width"] = px
    for field, mapping in (status or {}).items():
        key = "c%d" % len(ctx)
        opts["columnFormat"].setdefault(field, {}).update({
            "rowBackgroundColors": '> table | seriesByName("%s") | matchValue(%sBg)' % (field, key),
            "rowColors": '> table | seriesByName("%s") | matchValue(%sFg)' % (field, key)})
        ctx[key + "Bg"] = [{"match": v, "value": TINT[c][0]} for v, c in mapping.items()]
        ctx[key + "Fg"] = [{"match": v, "value": TINT[c][1]} for v, c in mapping.items()]
    ctx.update(extra_ctx or {})
    if not opts["columnFormat"]:
        del opts["columnFormat"]
    return opts, (ctx or None)


# ================================================================ data sources (single list)
# name -> use cases it serves. Drives the overview matrix and every dashboard's chips.
SOURCES = [
    ("Network", "DHCP server logs", {1, 4}),
    ("Network", "Switch & router syslog", {1, 4}),
    ("Network", "SNMP (interfaces & devices)", {4, 5}),
    ("Network", "Cisco ISE", {1, 4}),
    ("Network", "Catalyst Center (DNA) Assurance", {4}),
    ("Network", "Wireless LAN controller", {3, 4}),
    ("Network", "Firewall / proxy egress logs", {8}),
    ("Servers & virtualization", "Database & cluster logs", {2}),
    ("Servers & virtualization", "OS metrics (Windows / Linux)", {2}),
    ("Servers & virtualization", "Storage / SAN logs", {2}),
    ("Servers & virtualization", "VMware NSX Manager (DFW)", {6}),
    ("Servers & virtualization", "VMware vCenter events", {6}),
    ("Applications", "Application logs & health checks", {2, 6, 9}),
    ("Applications", "PACS / DICOM gateway logs", {1}),
    ("Clinical & facility", "IoT platform / gateway", {3, 5}),
    ("Clinical & facility", "Building management (BMS)", {5}),
    ("Clinical & facility", "Hospital information system (ADT / HL7)", {7}),
    ("Clinical & facility", "Bed management system", {7}),
    ("IT service management", "CMDB / asset inventory", {1, 3, 5, 6}),
    ("IT service management", "ServiceNow tickets & changes", {3, 6}),
    ("Cloud / security / AI", "CASB / secure web gateway", {8}),
    ("Cloud / security / AI", "DLP / data classification", {8, 9}),
    ("Cloud / security / AI", "Cloud audit logs (Azure / AWS / M365)", {8, 9}),
    ("Cloud / security / AI", "Identity (Entra ID / AD)", {8, 9}),
    ("Cloud / security / AI", "AI gateway / LLM proxy logs", {9}),
]


def sources_for(uc):
    return [name for _, name, ucs in SOURCES if uc in ucs]


USE_CASES = [
    (1, "dhcp_asset_drift", "Medical Device IP Drift", "X-ray unit lost its reserved IP to DHCP"),
    (2, "db_failover_monitoring", "Database Failover Insight", "Redundant DB failed over — nobody knew why"),
    (3, "iot_proactive_monitoring", "IoT Fleet Health", "IoT is 99% reactive, run by a partner"),
    (4, "network_correlation_ise_dna", "Network Correlation", "No correlation across ISE, DNA & switching"),
    (5, "environmental_device_health", "Environmental Device Health", "Label printer failed from rising heat"),
    (6, "nsx_microsegmentation_drift", "Microsegmentation Change Impact", "One click in NSX breaks the other side"),
    (7, "bed_occupancy", "Bed Occupancy", "Occupancy view for the executive board"),
    (8, "data_residency_compliance", "Swiss Data Residency", "Cloud OK if processed in Switzerland"),
    (9, "ai_observability", "AI Observability & Governance", "AI on hold due to uncertainty"),
]


# ================================================================ 1 · IP drift
def uc1():
    r = random.Random(1)
    d = Dash("dhcp_asset_drift", "1 · Medical Device IP Drift",
             "Detect when a medical device with a reserved IP silently receives a DHCP lease.")
    y = d.header(1, "Medical Device IP Drift",
                 "Reserved IPs silently replaced by DHCP leases — detected in minutes, with the switch port.",
                 "Ein Röntgengerät ist letzte Woche ausgestiegen, weil die IP-Adresse aus unerklärlichen "
                 "Gründen auf DHCP umgestellt wurde.")
    daily = [0, 1, 0, 0, 2, 0, 0, 1, 0, 0, 4, 1, 0, 1, 0, 2, 1, 0, 0, 3]
    y = d.kpis(y, [
        dict(title="Devices with reserved IP", data=ago(14, DAY, ["devices"], lambda k: (1231 + 4 * k + r.randint(-2, 2),)),
             under="imaging · lab · monitoring · infusion"),
        dict(title="Unexpected IP changes (7 days)", good="down", under="rolling 7-day count",
             data=ago(14, DAY, ["changes"], lambda k: (sum(daily[k:k + 7]),)),
             thresholds=[{"to": 1, "value": INK}, {"from": 1, "value": WARN}]),
        dict(title="Reserved devices on DHCP now", spark=None, good="down", under="need action",
             data=ago(2, HOUR, ["devices"], lambda k: ((2, 1)[k],)),
             thresholds=[{"to": 1, "value": GOOD}, {"from": 1, "value": CRIT}]),
        dict(title="Mean time to detect", unit=" min", trend="off",
             data=ago(12, WEEK, ["minutes"], lambda k: ([215, 190, 240, 205, 180, 12, 8, 6, 5, 4, 5, 4][k],)),
             under="was ~3.5 h before Splunk"),
    ])

    def dhcp(dow, hour):
        base = diurnal(hour, 55, 250) * (0.55 if dow in (0, 6) else 1)
        return (int(base * .62 + r.randint(-8, 8)), int(base * .26 + r.randint(-5, 5)),
                int(base * .12 + r.randint(-3, 3)), 0)
    ev = clock_hours(1, ["Renewal", "New lease", "Release", "Address conflict"], dhcp,
                     bumps=[("Address conflict", 3, 4, 3), ("Address conflict", 9, 10, 1),
                            ("Address conflict", 15, 16, 1), ("New lease", 3, 4, 12)])
    d.card(M, y, LEFT, 330, "splunk.column", ev, title="DHCP events per hour — medical device VLANs (24h)",
           options=column_opts([BLUE, AQUA, VIOLET, ORANGE]))
    cls = tbl(["Device class", "Changes"], [["Imaging (X-ray / CT / MRI)", 7], ["Lab analyzers", 4],
                                           ["Patient monitors", 3], ["Infusion pumps", 2], ["Other", 1]])
    d.card(M + LEFT + G, y, RIGHT, 330, "splunk.pie", cls,
           title="Unexpected IP changes by device class (30 days)", options=donut_opts())
    y += 330 + G

    t = tbl(["When", "Device", "Location", "Reserved IP", "Leased IP", "Switch port", "Status"], [
        [41, "CT-RAD-01", "Radiology B2-004", "10.40.12.11", "10.40.12.201", "sw-b2-acc-01 Gi1/0/3", "Open"],
        [182, "XR-RAD-03", "Radiology B2-012", "10.40.12.23", "10.40.12.187", "sw-b2-acc-02 Gi1/0/14", "Resolved"],
        [1310, "LAB-HEM-02", "Central lab C1", "10.40.30.42", "10.40.30.155", "sw-c1-acc-04 Gi2/0/22", "Resolved"],
        [2745, "MON-ICU-17", "ICU A3 bed 17", "10.40.51.117", "10.40.51.203", "sw-a3-acc-01 Gi1/0/40", "Resolved"],
        [3620, "INF-GW-02", "Surgery A2", "10.40.60.12", "10.40.60.99", "sw-a2-acc-03 Gi1/0/7", "Resolved"],
        [4410, "LAB-CHE-01", "Central lab C1", "10.40.30.21", "10.40.30.176", "sw-c1-acc-02 Gi1/0/9", "Resolved"],
        [8290, "XR-RAD-03", "Radiology B2-012", "10.40.12.23", "10.40.12.164", "sw-b2-acc-02 Gi1/0/14", "Resolved"],
    ], rel={"When": MIN})
    o, c = table_opts({"Status": {"Open": CRIT, "Resolved": GOOD}},
                      widths={"When": 90, "Device": 105, "Location": 150, "Reserved IP": 110, "Leased IP": 110,
                              "Switch port": 185, "Status": 90})
    d.card(M, y, LEFT, 300, "splunk.table", t, title="Reserved devices that received a DHCP lease (7 days)",
           options=o, context=c)

    def pacs(k):  # 5-min buckets, 6h. IP changed 180 min ago, fixed 120 min ago.
        return (int(36 + r.randint(-7, 9)) if 36 <= k < 48 else r.choice([0, 0, 0, 0, 1]),)
    d.card(M + LEFT + G, y, RIGHT, 300, "splunk.area", ago(72, MIN5, ["Failed image sends"], pacs),
           title="XR-RAD-03 — failed image sends to PACS (6h)", options=line_opts([ORANGE], legend="off"))
    y += 300 + G
    d.chips(y, sources_for(1))
    return d


# ================================================================ 2 · DB failover
def uc2():
    r = random.Random(2)
    d = Dash("db_failover_monitoring", "2 · Database Failover Insight",
             "Explain why a redundant database pair failed over, from storage, host, cluster and app signals.")
    y = d.header(2, "Database Failover Insight",
                 "The standby took over. Splunk lines up storage, host, cluster and app signals to show why.",
                 "Redundante Datenbankserver, die aktuell viel zu wenig überwacht werden. Es kann nicht "
                 "nachvollzogen werden, wieso sich das redundante System aktiviert hat.")
    y = d.kpis(y, [
        dict(title="Cluster availability (30 days)", unit="%", precision=2, good="up",
             data=ago(30, DAY, ["pct"], lambda k: (100.0 if k not in (6, 17, 29) else (99.91, 99.95, 99.97)[(6, 17, 29).index(k)],)),
             thresholds=[{"to": 99.9, "value": CRIT}, {"from": 99.9, "to": 99.95, "value": WARN}, {"from": 99.95, "value": GOOD}],
             under="KIS-DB-01 (SQL Server Always On)"),
        dict(title="Failovers (rolling 90 days)", good="down",
             data=ago(13, WEEK, ["n"], lambda k: ([3, 3, 3, 4, 4, 4, 3, 3, 3, 3, 3, 3, 4][k],)),
             under="2 unplanned · 2 patch windows"),
        dict(title="Replication lag now", unit=" s", precision=1, good="down",
             data=ago(60, MIN, ["lag"], lambda k: (0.3 + r.random() * 0.4 if k < 40 or k > 50 else 6 + r.random() * 4,)),
             under="db-sql-01b → db-sql-01a"),
        dict(title="Time to explain a failover", unit=" min", trend="off",
             data=ago(12, WEEK, ["m"], lambda k: ([1440, 960, 1200, 900, 1100, 25, 14, 9, 8, 7, 8, 7][k],)),
             under="was ~1 day of log hunting"),
    ])

    # 1-min buckets, 45 min window. Failover at k=31 (~14 min ago).
    def lat(k):
        return (int(360 + r.randint(-60, 160)) if 22 <= k <= 31 else int(4 + r.randint(0, 3)),)

    def cpu(k):
        a = 94 + r.randint(0, 5) if 25 <= k <= 31 else (9 + r.randint(0, 5) if k > 31 else 28 + r.randint(-4, 6))
        b = 44 + r.randint(-5, 8) if k > 31 else 14 + r.randint(-3, 4)
        return (a, b)

    def hb(k):
        return (1 if 28 <= k <= 30 else 0,)
    ch = 176
    d.card(M, y, LEFT, ch, "splunk.line", ago(45, MIN, ["db-sql-01a"], lat),
           title="Storage write latency (ms) — SAN path to db-sql-01a", options=line_opts([ORANGE], legend="off"))
    d.card(M, y + ch + G, LEFT, ch, "splunk.line", ago(45, MIN, ["db-sql-01a (primary)", "db-sql-01b (standby)"], cpu),
           title="CPU % on both nodes", options=line_opts([ORANGE, BLUE], legend="right", yAxisMax=100))
    d.card(M, y + 2 * (ch + G), LEFT, ch, "splunk.column", ago(45, MIN, ["Missed heartbeats"], hb),
           title="Cluster heartbeats missed", options=column_opts([ORANGE], legend="off", yAxisMax=3,
                                                                   yAxisMajorTickInterval=1))
    col_h = 3 * ch + 2 * G
    causes = tbl(["Cause", "Failovers"], [["Storage latency", 5], ["Planned patching", 4],
                                         ["Network heartbeat loss", 3], ["Memory pressure", 1]])
    pie_h = col_h - 180 - G
    d.card(M + LEFT + G, y, RIGHT, pie_h, "splunk.pie", causes, title="Failover causes (12 months)",
           options=donut_opts())
    d.kpis_at(M + LEFT + G, y + pie_h + G, RIGHT, 180, dict(
        title="Downtime of this failover", unit=" s", good="down",
        data=ago(12, WEEK, ["s"], lambda k: ([55, 48, 61, 40, 52, 47, 44, 39, 58, 45, 41, 42][k],)),
        under="target < 60 s · last 12 failovers"))
    y += col_h + G
    tl = tbl(["When", "Layer", "System", "Event"], [
        [23, "Storage", "san-ctrl-b", "Path failover on controller B"],
        [22, "Host", "db-sql-01a", "Disk write latency 480 ms (normal 4 ms)"],
        [20, "Host", "db-sql-01a", "CPU 97% — mostly I/O wait"],
        [17, "Cluster", "KIS-AG-01", "Heartbeat missed (1 of 3)"],
        [15, "Cluster", "KIS-AG-01", "Heartbeat missed (3 of 3) — failover"],
        [14, "Cluster", "db-sql-01b", "Promoted to primary"],
        [13, "App", "kis-app-01..04", "214 client reconnects in 40 s"],
        [5, "Cluster", "db-sql-01a", "Re-joined as secondary — in sync"],
    ], rel={"When": MIN})
    layer = {"Storage": SERIOUS, "Host": WARN, "Cluster": CRIT, "App": MUTED}
    o, c = table_opts({"Layer": layer}, widths={"When": 110, "Layer": 110, "System": 170, "Event": 960})
    th = 76 + 29 * 9
    d.card(M, y, INNER, th, "splunk.table", tl, title="What happened — root cause timeline across all layers",
           options=o, context=c)
    y += th + G
    d.chips(y, sources_for(2))
    return d


# ================================================================ 3 · IoT
def uc3():
    r = random.Random(3)
    d = Dash("iot_proactive_monitoring", "3 · IoT Fleet Health",
             "Move IoT monitoring from reactive to proactive, shared with the external service partner.")
    y = d.header(3, "IoT Fleet Health — Reactive to Proactive",
                 "2,400 connected devices run with a partner. Splunk flags degrading devices days before they fail.",
                 "IoT Systeme werden zur Zeit überhaupt nicht proaktiv überwacht, zu 99% reaktiv. Man arbeitet "
                 "hier mit einem externen Dienstleister zusammen.")
    y = d.kpis(y, [
        dict(title="Connected IoT devices", data=ago(30, DAY, ["n"], lambda k: (2331 + 3 * k + r.randint(-3, 3),)),
             under="tags · sensors · pumps · nurse call"),
        dict(title="Online now", unit="%", precision=1, good="up",
             data=ago(24, HOUR, ["pct"], lambda k: (98.1 + r.random() * 0.8 - (1.9 if k == 15 else 0),)),
             thresholds=[{"to": 95, "value": CRIT}, {"from": 95, "to": 98, "value": WARN}, {"from": 98, "value": GOOD}],
             under="last 24 hours"),
        dict(title="At risk within 7 days", good="down",
             data=ago(14, DAY, ["n"], lambda k: (58 - k * 2 + r.randint(-3, 3),)),
             thresholds=[{"to": 20, "value": INK}, {"from": 20, "value": WARN}], under="battery · signal · heartbeat"),
        dict(title="Partner tickets opened proactively", unit="%", good="up",
             data=ago(12, WEEK, ["pct"], lambda k: ([1, 1, 2, 1, 1, 18, 31, 42, 49, 55, 61, 64][k],)),
             under="was ~1% before"),
    ])

    def offline(dow, hour):
        return (int(diurnal(hour, 34, 18, peak=3) + r.randint(-3, 3)), int(5 + r.randint(-2, 2)),
                int(3 + r.randint(-1, 2)), int(2 + r.randint(-1, 1)))
    ap_down = [("Asset tags (RTLS)", 44, 47, 18), ("Environment sensors", 44, 47, 6), ("Nurse call buttons", 44, 47, 6)]
    d.card(M, y, LEFT, 330, "splunk.line",
           clock_hours(7, ["Asset tags (RTLS)", "Environment sensors", "Infusion pump gateways", "Nurse call buttons"],
                       offline, bumps=ap_down),
           title="Offline devices by type — last 7 days", options=line_opts([BLUE, AQUA, ORANGE, VIOLET]))
    health = tbl(["State", "Devices"], [["Healthy", 2236], ["Low battery", 88], ["Weak signal", 51], ["Offline", 37]])
    d.card(M + LEFT + G, y, RIGHT, 330, "splunk.pie", health, title="Fleet health right now",
           options=donut_opts([AQUA, YELLOW, BLUE, ORANGE]))
    y += 330 + G

    t = tbl(["Device", "Type", "Location", "Battery", "Signal", "Predicted", "Partner ticket", "Risk"], [
        ["INF-GW-22", "Infusion pump gateway", "Surgery A2", "—", "-91 dBm", "< 24 h", "INC0048812", "High"],
        ["NC-A3-114", "Nurse call button", "Internal med. A3 R114", "9%", "-72 dBm", "1 day", "INC0048809", "High"],
        ["TAG-00871", "Asset tag (bed)", "Orthopedics B3", "11%", "-68 dBm", "2 days", "INC0048790", "Medium"],
        ["ENV-C1-104", "Temp. sensor", "Lab storage C1-104", "14%", "-77 dBm", "3 days", "INC0048785", "Medium"],
        ["TAG-01322", "Asset tag (pump)", "ICU A3", "17%", "-70 dBm", "4 days", "planned", "Low"],
        ["NC-B1-022", "Nurse call button", "Maternity B1 R22", "19%", "-83 dBm", "5 days", "planned", "Low"],
    ])
    o, c = table_opts({"Risk": {"High": CRIT, "Medium": WARN, "Low": MUTED}},
                      widths={"Device": 100, "Type": 170, "Location": 170, "Battery": 70, "Signal": 80,
                              "Predicted": 85, "Partner ticket": 115, "Risk": 80})
    d.card(M, y, LEFT, 300, "splunk.table", t, title="Next likely failures — ticket opened before the outage",
           options=o, context=c)
    tickets = ago(12, WEEK, ["Reactive (after failure)", "Proactive (before failure)"],
                  lambda k: ([61, 58, 63, 57, 60, 44, 38, 31, 27, 24, 21, 19][k], [0, 1, 1, 0, 1, 10, 17, 22, 26, 29, 33, 34][k]))
    d.card(M + LEFT + G, y, RIGHT, 300, "splunk.column", tickets, title="Partner tickets per week",
           options=column_opts([ORANGE, BLUE]))
    y += 300 + G
    d.chips(y, sources_for(3))
    return d


# ================================================================ 4 · network correlation
def uc4():
    r = random.Random(4)
    d = Dash("network_correlation_ise_dna", "4 · Network Correlation",
             "Correlate Cisco ISE, Catalyst Center (DNA) and switching into one incident timeline.")
    y = d.header(4, "Network Correlation — ISE · Catalyst Center · Switching",
                 "One timeline across identity, assurance and the wire — instead of three consoles.",
                 "Fehlende Korrelationen der Netzwerkkomponenten wie ISE, DNA usw.")
    y = d.kpis(y, [
        dict(title="802.1X auth failures today", good="down",
             data=ago(14, DAY, ["n"], lambda k: (int(260 + r.randint(-40, 60) + (90 if k == 13 else 0)),)),
             under="all sites"),
        dict(title="Clients with poor health", good="down",
             data=ago(24, HOUR, ["n"], lambda k: (int(18 + r.randint(-5, 6) + (60 if k == 20 else 0) + (12 if k == 21 else 0)),)),
             thresholds=[{"to": 40, "value": INK}, {"from": 40, "value": WARN}],
             under="Catalyst Center health < 4"),
        dict(title="Correlated incidents (7 days)", good="down",
             data=ago(14, DAY, ["n"], lambda k: ([9, 8, 8, 7, 9, 10, 8, 7, 6, 6, 7, 5, 6, 6][k],)),
             under="auto-grouped from 3 sources"),
        dict(title="Mean time to root cause", unit=" min", trend="off",
             data=ago(12, WEEK, ["m"], lambda k: ([130, 115, 140, 125, 120, 22, 16, 13, 12, 11, 12, 11][k],)),
             under="was ~2 h before Splunk"),
    ])

    # 5-min buckets over 4h; incident 40-50 min ago (k=38..40), recovered ~20 min ago.
    def auth(k):
        return (int(46 + r.randint(-6, 8)) if 38 <= k <= 40 else int(5 + r.randint(-2, 3)),)

    def health(k):
        return (round(3.1 + r.random() * .5, 1) if 38 <= k <= 43 else round(8.6 + r.random() * .5, 1),)

    def flaps(k):
        return (int(11 + r.randint(-3, 3)) if 37 <= k <= 40 else 0,)
    ch = 176
    d.card(M, y, LEFT, ch, "splunk.column", ago(48, MIN5, ["Auth failures"], auth),
           title="Cisco ISE — 802.1X auth failures (Ward A3 West, per 5 min)", options=column_opts([ORANGE], legend="off"))
    d.card(M, y + ch + G, LEFT, ch, "splunk.line", ago(48, MIN5, ["Client health"], health),
           title="Catalyst Center — average client health (Ward A3 West, 0–10)",
           options=line_opts([BLUE], legend="off", yAxisMin=0, yAxisMax=10))
    d.card(M, y + 2 * (ch + G), LEFT, ch, "splunk.column", ago(48, MIN5, ["Interface flaps"], flaps),
           title="Switch sw-a3-acc-01 — uplink Te1/1/1 up/down events", options=column_opts([VIOLET], legend="off"))
    tl = tbl(["When", "Source", "Event"], [
        [51, "Switch", "sw-a3-acc-01 Te1/1/1 flapping (CRC errors)"],
        [50, "ISE", "RADIUS timeouts from sw-a3-acc-01"],
        [49, "ISE", "38 × 802.1X auth failed — Ward A3 West"],
        [48, "Catalyst Center", "Client health A3 West 8.9 → 3.2"],
        [46, "WLC", "22 clients roamed to AP-A3-W-07"],
        [27, "Switch", "Te1/1/1 stable after SFP swap"],
        [24, "Catalyst Center", "Client health back to 8.7"],
    ], rel={"When": MIN})
    col_h = 3 * ch + 2 * G
    reasons = tbl(["Reason", "Failures"], [["RADIUS timeout", 96], ["Certificate expired", 71],
                                           ["Wrong credentials", 64], ["Posture non-compliant", 49],
                                           ["Unknown endpoint", 32]])
    pie_h = (col_h - G) // 2
    d.card(M + LEFT + G, y, RIGHT, pie_h, "splunk.pie", reasons, title="Auth failure reasons (24h)",
           options=donut_opts())
    sw = tbl(["Switch", "Events"], [["sw-a3-acc-01", 184], ["sw-b2-acc-02", 61], ["sw-c1-acc-04", 38],
                                    ["sw-a2-acc-03", 27], ["sw-d1-acc-01", 19]])
    d.card(M + LEFT + G, y + pie_h + G, RIGHT, col_h - pie_h - G, "splunk.bar", sw,
           title="Switches with most related events (24h)", options=bar_opts(VIOLET))
    y += col_h + G
    src_color = [{"match": "ISE", "value": ORANGE}, {"match": "Catalyst Center", "value": BLUE},
                 {"match": "Switch", "value": VIOLET}, {"match": "WLC", "value": AQUA}]
    o, c = table_opts(widths={"When": 110, "Source": 170, "Event": 1080}, extra_ctx={"src": src_color})
    o["columnFormat"]["Source"]["rowColors"] = '> table | seriesByName("Source") | matchValue(src)'
    th = 76 + 29 * 8
    d.card(M, y, INNER, th, "splunk.table", tl,
           title="Correlated incident — one timeline instead of three consoles", options=o, context=c)
    y += th + G
    d.chips(y, sources_for(4))
    return d


# ================================================================ 5 · environmental
def uc5():
    r = random.Random(5)
    d = Dash("environmental_device_health", "5 · Environmental Device Health",
             "Correlate room temperature with device errors to prevent heat-related failures.")
    y = d.header(5, "Environmental Device Health",
                 "A label printer failed from heat. Splunk joins room climate with device errors and warns early.",
                 "Etikettendrucker, der dieses Jahr aufgrund steigender Temperaturen nicht mehr funktioniert hat.")
    y = d.kpis(y, [
        dict(title="Rooms monitored", data=ago(30, DAY, ["n"], lambda k: (64 + k * 3 // 4,)),
             under="labs · pharmacy · IT rooms · storage"),
        dict(title="Rooms above limit now", spark=None, good="down",
             data=ago(2, HOUR, ["n"], lambda k: ((1, 2)[k],)),
             thresholds=[{"to": 1, "value": GOOD}, {"from": 1, "value": CRIT}], under="C1-104 · B1-012"),
        dict(title="Label printer errors (7 days)", good="down",
             data=ago(14, DAY, ["n"], lambda k: ([6, 5, 7, 6, 8, 9, 12, 15, 21, 27, 33, 38, 44, 47][k],)),
             thresholds=[{"to": 20, "value": INK}, {"from": 20, "value": WARN}], under="rolling 7-day count"),
        dict(title="Hottest room now", unit="°C", precision=1, good="down",
             data=ago(24, HOUR, ["t"], lambda k: (31.8 if k == 23 else 28.9 + 2.8 * k / 23 + (r.random() - .5) * .5,)),
             thresholds=[{"to": 28, "value": GOOD}, {"from": 28, "to": 30, "value": WARN}, {"from": 30, "value": CRIT}],
             under="Lab storage C1-104 (printer room)"),
    ])

    def temp(dow, hour):
        return (round(diurnal(hour, 24.0, 27.5, 16) + r.random() * .4, 1),
                round(diurnal(hour, 23.0, 25.5, 16) + r.random() * .3, 1),
                round(21.0 + r.random() * .6, 1), 30)
    heat = [("Lab storage C1-104", 4.5), ("Pharmacy B1-012", 3.0)]      # heatwave building over the week
    hvac = [("Server room A0-001", 30, 34, 2.5)]                         # brief cooling fault
    d.card(M, y, LEFT, 330, "splunk.line",
           clock_hours(7, ["Lab storage C1-104", "Pharmacy B1-012", "Server room A0-001", "Printer max. rating"], temp,
                       ramps=heat, bumps=hvac),
           title="Room temperature (°C) — last 7 days",
           options=line_opts([ORANGE, BLUE, AQUA, MUTED], lineDashStylesByField={"Printer max. rating": "dash"}))
    d.card(M + LEFT + G, y, RIGHT, 330, "splunk.markergauge",
           ago(2, HOUR, ["C1-104"], lambda k: ((30.9, 31.8)[k],)), title="Lab storage C1-104 — now (°C)",
           options={"orientation": "horizontal", "majorTickInterval": 5, "gaugeRanges": [
               {"from": 15, "to": 26, "value": GOOD}, {"from": 26, "to": 30, "value": WARN},
               {"from": 30, "to": 40, "value": CRIT}]})
    y += 330 + G

    t = tbl(["Device", "Type", "Room", "Temp now", "Rated max", "Errors today", "Status"], [
        ["PRT-LAB-04", "Label printer", "Lab storage C1-104", "31.8 °C", "30 °C", 9, "Over limit"],
        ["PRT-PHA-01", "Label printer", "Pharmacy B1-012", "30.4 °C", "30 °C", 4, "Over limit"],
        ["FRG-PHA-02", "Medication fridge", "Pharmacy B1-012", "6.8 °C", "8 °C", 0, "Near limit"],
        ["UPS-A0-01", "UPS", "Server room A0-001", "23.1 °C", "35 °C", 0, "OK"],
        ["PRT-ED-02", "Label printer", "Emergency D0-003", "25.6 °C", "30 °C", 0, "OK"],
        ["INC-LAB-01", "Incubator", "Microbiology C1-110", "37.1 °C", "37.5 °C", 0, "OK"],
    ])
    o, c = table_opts({"Status": {"Over limit": CRIT, "Near limit": WARN, "OK": GOOD}},
                      widths={"Device": 105, "Type": 140, "Room": 170, "Temp now": 90, "Rated max": 90,
                              "Errors today": 100, "Status": 95})
    d.card(M, y, LEFT, 300, "splunk.table", t, title="Devices vs. their rated operating range", options=o, context=c)
    errs = ago(14, DAY, ["Print head overheat", "Label jam", "Ribbon / media"],
               lambda k: (max(0, (k - 6) * (k - 5) // 6 + r.randint(0, 1)), r.randint(0, 2), r.randint(0, 1)))
    d.card(M + LEFT + G, y, RIGHT, 300, "splunk.column", errs, title="Label printer errors per day",
           options=column_opts([ORANGE, BLUE, VIOLET]))
    y += 300 + G
    d.chips(y, sources_for(5))
    return d


# ================================================================ 6 · NSX
def uc6():
    r = random.Random(6)
    d = Dash("nsx_microsegmentation_drift", "6 · Microsegmentation Change Impact",
             "Tie every connectivity break in VMware NSX back to the rule or port change that caused it.")
    y = d.header(6, "Microsegmentation Change Impact — VMware NSX",
                 "A small rule or port change breaks an app on the other side. Splunk links each break to its change.",
                 "Ein kleiner Klick und es tut auf der anderen Seite wieder nichts — dann hat der Lieferant "
                 "wieder einen Port gewechselt.")
    y = d.kpis(y, [
        dict(title="DFW rule changes (7 days)", data=ago(14, DAY, ["n"], lambda k: ([19, 21, 20, 24, 22, 18, 17, 19, 23, 26, 25, 22, 24, 23][k],)),
             under="rolling 7 days · rules · groups"),
        dict(title="Changes followed by an outage", good="down",
             data=ago(12, WEEK, ["n"], lambda k: ([5, 6, 4, 7, 5, 6, 4, 3, 3, 2, 3, 2][k],)),
             thresholds=[{"to": 1, "value": GOOD}, {"from": 1, "value": WARN}], under="per week"),
        dict(title="Changes without a ticket", good="down",
             data=ago(12, WEEK, ["n"], lambda k: ([9, 8, 10, 7, 8, 6, 5, 4, 4, 3, 3, 2][k],)),
             under="per week · mostly external vendors"),
        dict(title="Time to find the causing change", unit=" min", trend="off",
             data=ago(12, WEEK, ["m"], lambda k: ([180, 150, 210, 160, 170, 14, 9, 7, 6, 6, 5, 6][k],)),
             under="was hours of guessing"),
    ])

    def blocked(k):  # 5-min buckets over 6h; change 150 min ago, revert 95 min ago
        return (int(390 + r.randint(-40, 40)) if 42 <= k <= 52 else int(3 + r.randint(0, 4)),)
    d.card(M, y, LEFT, 330, "splunk.area", ago(72, MIN5, ["Blocked connections"], blocked),
           title="Blocked connections per 5 min — LIS app tier → LIS database (6h)",
           options=line_opts([ORANGE], legend="off"))
    who = tbl(["Changed by", "Changes"], [["External vendors", 41], ["Network team", 27],
                                         ["Application teams", 18], ["Automation (vRA)", 22]])
    d.card(M + LEFT + G, y, RIGHT, 330, "splunk.pie", who, title="Who changes NSX rules (30 days)", options=donut_opts())
    y += 330 + G

    t = tbl(["When", "Changed by", "Object", "Change", "Ticket", "Impact"], [
        [95, "net-admin j.keller", "Rule LIS-App-to-DB", "Reverted to 1433", "CHG0041877", "Restored"],
        [152, "vendor-lis (ext.)", "Rule LIS-App-to-DB", "Port 1433 → 14330", "none", "Outage 57 min"],
        [410, "app-pacs m.brunner", "Group SG-PACS-Web", "Added VM pacs-web-03", "CHG0041862", "None"],
        [760, "automation (vRA)", "Segment Seg-Lab-12", "New segment + rules", "CHG0041850", "None"],
        [1930, "vendor-lis (ext.)", "Rule LIS-Interfaces", "Source range narrowed", "none", "Outage 12 min"],
        [2880, "net-admin s.frei", "Service HL7-MLLP", "Added port 2575", "CHG0041811", "None"],
    ], rel={"When": MIN})
    o, c = table_opts({"Impact": {"Outage 57 min": CRIT, "Outage 12 min": CRIT, "Restored": GOOD, "None": MUTED},
                       "Ticket": {"none": WARN}})
    d.card(M, y, LEFT, 300, "splunk.table", t, title="Change log — with the impact that followed", options=o, context=c)
    top = tbl(["Destination", "Blocked"], [["LIS DB :14330", 4212], ["HL7 engine :2575", 388],
                                           ["PACS DICOM :104", 121], ["AD (SMB) :445", 64]])
    d.card(M + LEFT + G, y, RIGHT, 300, "splunk.bar", top, title="Top blocked destinations after changes (24h)",
           options=bar_opts(ORANGE))
    y += 300 + G
    d.chips(y, sources_for(6))
    return d


# ================================================================ 7 · bed occupancy
DEPTS = [("Surgery", 96), ("Internal Medicine", 118), ("Cardiology", 52), ("Orthopedics", 64),
         ("Intensive Care", 18), ("Pediatrics", 34), ("Maternity", 30)]


def uc7():
    r = random.Random(7)
    beds = sum(b for _, b in DEPTS)
    d = Dash("bed_occupancy", "7 · Bed Occupancy",
             "Executive view of bed occupancy, patient flow and capacity by department.")
    y = d.header(7, "Bed Occupancy — Executive View",
                 "Live occupancy, patient flow and capacity by department — for the executive board.",
                 "Überwachung der Bettenauslastung, kann interessant sein für die GL.")
    y = d.kpis(y, [
        dict(title="Occupancy now", unit="%", precision=1,
             data=ago(30, DAY, ["pct"], lambda k: (86.9 if k == 29 else 84 + 4 * math.sin(k / 4.5) + r.random() * 1.5,)),
             thresholds=[{"to": 85, "value": GOOD}, {"from": 85, "to": 92, "value": WARN}, {"from": 92, "value": CRIT}],
             under="%d beds in total" % beds),
        dict(title="Free beds now", good="up", data=ago(24, HOUR, ["n"], lambda k: (54 if k == 23 else int(diurnal(k, 40, 62, 11) + r.randint(-3, 3)),)),
             under="incl. 1 ICU bed"),
        dict(title="Admissions today", data=clock_days(14, ["n"], lambda dow: ((50 if dow in (0, 6) else 74) + r.randint(-6, 6),)),
             under="weekdays ~74 · weekends ~50"),
        dict(title="Avg. length of stay", unit=" days", precision=1, good="down",
             data=ago(12, WEEK, ["d"], lambda k: ((5.4, 5.3)[k - 10] if k >= 10 else 5.7 - k * .03 + r.random() * .12,)),
             under="target 5.0"),
    ])

    def occ(dow):
        wkend = dow in (0, 6)
        return (round(88 - (9 if wkend else 0) + r.uniform(-2, 2), 1),        # surgery: elective dip
                round(93 + r.uniform(-2, 3), 1),
                round(85 - (4 if wkend else 0) + r.uniform(-3, 3), 1),
                round(82 - (12 if wkend else 0) + r.uniform(-2, 2), 1),
                round(89 + r.uniform(-5, 6), 1))
    d.card(M, y, LEFT, 330, "splunk.line",
           clock_days(30, ["Surgery", "Internal Medicine", "Cardiology", "Orthopedics", "Intensive Care"], occ),
           title="Occupancy % by department — last 30 days",
           options=line_opts([BLUE, ORANGE, AQUA, YELLOW, MAGENTA], yAxisMin=60, yAxisMax=100))
    d.card(M + LEFT + G, y, RIGHT, 330, "splunk.singlevalueradial",
           ago(2, HOUR, ["pct"], lambda k: ((89, 94)[k],)), title="Intensive care occupancy now",
           options={"maxValue": 100, "minValue": 0, "unit": "%", "majorColor": INK,
                    "radialBackgroundColor": "#2A313C",
                    "radialStrokeColor": "> majorValue | rangeValue(radialConfig)", "trendDisplay": "off"},
           context={"radialConfig": [{"to": 85, "value": GOOD}, {"from": 85, "to": 92, "value": WARN},
                                     {"from": 92, "value": CRIT}]})
    y += 330 + G

    rows = []
    status = {}
    for name, b in DEPTS:
        occ_now = {"Surgery": 87, "Internal Medicine": 113, "Cardiology": 44, "Orthopedics": 51,
                   "Intensive Care": 17, "Pediatrics": 24, "Maternity": 22}[name]
        pct = round(occ_now / b * 100, 1)
        s = "Full" if pct >= 92 else ("High" if pct >= 85 else "OK")
        status[s] = {"Full": CRIT, "High": WARN, "OK": GOOD}[s]
        waiting = {"Internal Medicine": 6, "Surgery": 2, "Intensive Care": 1}.get(name, 0)
        rows.append([name, b, occ_now, b - occ_now, pct, waiting, s])
    t = tbl(["Department", "Beds", "Occupied", "Free", "Occupancy %", "Waiting for bed", "Status"], rows)
    o, c = table_opts({"Status": status}, font="default")
    d.card(M, y, LEFT, 372, "splunk.table", t, title="Departments right now", options=o, context=c)
    flow = clock_days(14, ["Admissions", "Discharges"],
                      lambda dow: ((50 if dow in (0, 6) else 74) + r.randint(-6, 6),
                                        (38 if dow in (0, 6) else 71) + (8 if dow == 5 else 0) + r.randint(-6, 6)))
    d.card(M + LEFT + G, y, RIGHT, 372, "splunk.column", flow, title="Admissions vs. discharges per day",
           options=column_opts([BLUE, AQUA], stacked=False))
    y += 372 + G
    d.chips(y, sources_for(7))
    return d


# ================================================================ 8 · data residency
def uc8():
    r = random.Random(8)
    d = Dash("data_residency_compliance", "8 · Swiss Data Residency",
             "Prove that every cloud transfer was processed in Switzerland, and flag the ones that were not.")
    y = d.header(8, "Swiss Data Residency — Cloud Compliance",
                 "Policy: cloud is fine if data is processed in Switzerland. Splunk proves it for every transfer.",
                 "Solange es in der Schweiz verarbeitet wird, darf es auch in die Cloud raus.")
    y = d.kpis(y, [
        dict(title="Cloud transfers today", data=clock_days(14, ["n"], lambda dow: (int((8200 if dow in (0, 6) else 18400) + r.randint(-900, 900)),)),
             under="M365 · Azure · AWS · SaaS"),
        dict(title="Processed in Switzerland", unit="%", precision=2, good="up",
             data=ago(14, DAY, ["pct"], lambda k: (99.94 if k == 13 else 99.9 + r.random() * .09,)),
             thresholds=[{"to": 99.5, "value": CRIT}, {"from": 99.5, "to": 99.9, "value": WARN}, {"from": 99.9, "value": GOOD}],
             under="of all transfers today"),
        dict(title="Sensitive data transfers today", data=clock_days(14, ["n"], lambda dow: (int((1400 if dow in (0, 6) else 3100) + r.randint(-200, 200)),)),
             under="patient · personnel · finance"),
        dict(title="Policy violations (7 days)", good="down",
             data=ago(14, DAY, ["n"], lambda k: ([4, 4, 5, 3, 3, 3, 2, 2, 3, 2, 2, 1, 2, 2][k],)),
             thresholds=[{"to": 1, "value": GOOD}, {"from": 1, "value": CRIT}], under="rolling 7-day count"),
    ])

    def region(dow, hour):
        f = .45 if dow in (0, 6) else 1
        base = diurnal(hour, 90, 1450, 11) * f
        return (int(base * .74 + r.randint(-20, 20)), int(base * .23 + r.randint(-10, 10)),
                int(max(0, base * .004 + r.randint(-1, 2))), 0)
    d.card(M, y, LEFT, 330, "splunk.column",
           clock_hours(1, ["Switzerland North (Zurich)", "Switzerland West (Geneva)", "EU (Frankfurt)", "Other"], region,
                       bumps=[("EU (Frankfurt)", 0, 2, 9), ("Other", 5, 6, 2), ("Other", 7, 8, 2)]),
           title="Cloud transfers per hour by processing region (24h)", options=column_opts([BLUE, AQUA, ORANGE, MAGENTA]))
    svc = tbl(["Service", "GB"], [["Microsoft 365 (CH)", 412], ["Azure Switzerland North", 286],
                                  ["AWS Zurich", 97], ["Radiology AI (SaaS)", 12], ["Other SaaS", 9]])
    d.card(M + LEFT + G, y, RIGHT, 330, "splunk.pie", svc, title="Data volume by cloud service (24h, GB)",
           options=donut_opts())
    y += 330 + G

    t = tbl(["When", "Application", "Data class", "Destination", "Region", "Volume", "Status"], [
        [58, "Radiology AI (SaaS)", "Patient images", "api.rad-ai.example", "EU (Frankfurt)", "1.2 GB", "Violation"],
        [196, "Radiology AI (SaaS)", "Patient images", "api.rad-ai.example", "EU (Frankfurt)", "0.8 GB", "Violation"],
        [310, "HR recruiting tool", "Personnel data", "eu.recruit.example", "EU (Dublin)", "14 MB", "Exception"],
        [455, "Microsoft Teams", "Internal", "teams.microsoft.com", "EU (Amsterdam)", "220 MB", "Exception"],
        [612, "Lab supplier portal", "Order data", "orders.labsupply.example", "EU (Vienna)", "3 MB", "Exception"],
    ], rel={"When": MIN})
    o, c = table_opts({"Status": {"Violation": CRIT, "Exception": MUTED}},
                      widths={"When": 90, "Application": 160, "Data class": 120, "Destination": 190,
                              "Region": 130, "Volume": 75, "Status": 95})
    d.card(M, y, LEFT, 300, "splunk.table", t, title="Transfers processed outside Switzerland (24h)", options=o, context=c)
    reg = tbl(["Region", "Sensitive transfers"], [["Switzerland", 21540], ["EU", 38], ["USA", 0]])
    d.card(M + LEFT + G, y, RIGHT, 300, "splunk.bar", reg, title="Sensitive data transfers by region (7 days)",
           options=bar_opts(BLUE))
    y += 300 + G
    d.chips(y, sources_for(8))
    return d


# ================================================================ 9 · AI observability
def uc9():
    r = random.Random(9)
    d = Dash("ai_observability", "9 · AI Observability & Governance",
             "See AI usage, cost, latency and guardrail findings in one place.")
    y = d.header(9, "AI Observability & Governance",
                 "What AI is used for, what it costs and what the guardrails catch — evidence to release the handbrake.",
                 "AI Observability ist sehr interessant, GL hat da zur Zeit aber etwas die Handbremse drauf, "
                 "aufgrund Unsicherheit.")
    y = d.kpis(y, [
        dict(title="AI requests today", data=clock_days(30, ["n"], lambda dow: (int((2400 if dow in (0, 6) else 6800) + r.randint(-300, 300)),)),
             under="4 applications · 1,180 users"),
        dict(title="Cost this month", unit="CHF ", unit_pos="before", trend="off",
             data=ago(23, DAY, ["chf"], lambda k: (int(215 * (k + 1) + r.randint(-30, 30)),)),
             under="budget CHF 7,500"),
        dict(title="p95 response time", unit=" s", precision=1, good="down",
             data=ago(24, HOUR, ["s"], lambda k: (2.1 + r.random() * .6 + (0.9 if k == 10 else 0),)),
             thresholds=[{"to": 3, "value": GOOD}, {"from": 3, "to": 5, "value": WARN}, {"from": 5, "value": CRIT}],
             under="target < 3 s"),
        dict(title="Guardrail blocks (7 days)", good="down",
             data=ago(14, DAY, ["n"], lambda k: ([31, 29, 27, 26, 24, 23, 22, 21, 20, 19, 18, 17, 18, 17][k],)),
             thresholds=[{"to": 1, "value": GOOD}, {"from": 1, "value": WARN}], under="PHI · PII · injection"),
    ])

    def reqs(dow):
        f = .35 if dow in (0, 6) else 1
        return (int(2600 * f + r.randint(-120, 120)), int(1500 * f + r.randint(-90, 90)),
                int(800 * f + r.randint(-60, 60)), int(420 * f + r.randint(-40, 40)))
    d.card(M, y, LEFT, 330, "splunk.line",
           clock_days(30, ["Radiology report assistant", "Clinical documentation", "IT helpdesk bot", "Translation"], reqs,
                      ramps=[("Radiology report assistant", 900), ("Clinical documentation", 600)]),
           title="Requests per day by application (30 days)", options=line_opts([BLUE, ORANGE, AQUA, YELLOW]))
    find = tbl(["Finding", "Count"], [["Patient data (PHI)", 41], ["Personal data (PII)", 27],
                                     ["Confidential documents", 12], ["Prompt injection attempt", 6]])
    d.card(M + LEFT + G, y, RIGHT, 330, "splunk.pie", find, title="Guardrail findings (30 days)", options=donut_opts())
    y += 330 + G

    t = tbl(["When", "Application", "User group", "Finding", "Action", "Model"], [
        [12, "Clinical documentation", "Nursing", "Patient name + DOB in prompt", "Redacted", "GPT-4o (Azure CH)"],
        [47, "Translation", "Admin staff", "Insurance number", "Redacted", "GPT-4o mini (Azure CH)"],
        [95, "IT helpdesk bot", "External", "Prompt injection attempt", "Blocked", "GPT-4o mini (Azure CH)"],
        [230, "Radiology report assistant", "Radiology", "Report sent to non-CH endpoint", "Blocked", "Vendor model (EU)"],
        [415, "Clinical documentation", "Physicians", "Full discharge letter", "Flagged", "GPT-4o (Azure CH)"],
    ], rel={"When": MIN})
    o, c = table_opts({"Action": {"Blocked": CRIT, "Redacted": WARN, "Flagged": MUTED}},
                      widths={"When": 90, "Application": 185, "User group": 100, "Finding": 220,
                              "Action": 90, "Model": 165})
    d.card(M, y, LEFT, 300, "splunk.table", t, title="Latest guardrail events", options=o, context=c)
    cost = tbl(["Model", "CHF"], [["GPT-4o", 2940], ["Llama (on-prem)", 1090], ["GPT-4o mini", 610], ["Embeddings", 180]])
    d.card(M + LEFT + G, y, RIGHT, 300, "splunk.bar", cost, title="Cost by model this month (CHF)",
           options=bar_opts(BLUE))
    y += 300 + G
    d.chips(y, sources_for(9))
    return d


# ================================================================ overview
def overview():
    d = Dash("overview", "Customer Example \u2014 Overview",
             "Nine challenges from the discovery workshop, the dashboard for each, and the data Splunk needs.")
    y = d.header(0, "What Splunk can do for %s" % HOSPITAL,
                 "Nine challenges from our workshop \u2014 each with a dashboard, and the data it needs.")
    cw = (INNER - 2 * G) // 3
    ch = 112
    for i, (n, view, title, pain) in enumerate(USE_CASES):
        x = M + (i % 3) * (cw + G)
        cy = y + (i // 3) * (ch + G)
        d.rect(x, cy, cw, ch)
        d.rect(x + 18, cy + 20, 40, 40, fill="#12343A", stroke=ACCENT, rx=20)
        d.md(x + 28, cy + 28, 24, 24, "**%d**" % n, size=16, color=ACCENT)
        d.md(x + 72, cy + 16, cw - 88, 28, "**%s**" % title, size=17, color=INK)
        d.md(x + 72, cy + 44, cw - 88, 24, pain, size=13, color=INK_2)
        d.md(x + 72, cy + 74, cw - 88, 24, "[Open dashboard \u2192](/app/%s/%s)" % (APP, view), size=13)
    y += 3 * (ch + G)

    short = {1: "IP drift", 2: "DB", 3: "IoT", 4: "Network", 5: "Climate", 6: "NSX", 7: "Beds", 8: "Cloud", 9: "AI"}
    per_uc = tbl(["Use case", "Data sources"],
                 [["%d \u00b7 %s" % (n, short[n]), len(sources_for(n))] for n, *_ in USE_CASES])
    d.card(M, y, LEFT, 300, "splunk.column", per_uc, title="Data sources needed per use case",
           options=column_opts([ACCENT], stacked=False, legend="off", dataValuesDisplay="all"))
    by_cat = {}
    for cat, _, _ in SOURCES:
        by_cat[cat] = by_cat.get(cat, 0) + 1
    d.card(M + LEFT + G, y, RIGHT, 300, "splunk.pie",
           tbl(["Category", "Sources"], [[k, v] for k, v in by_cat.items()]),
           title="%d data sources by category" % len(SOURCES), options=donut_opts())
    y += 300 + G

    ucs = [(n, "%d %s" % (n, short[n])) for n, *_ in USE_CASES]
    cols = ["Data source", "Category"] + [c for _, c in ucs]
    rows = [[name, cat] + ["\u2713" if n in used else "" for n, _ in ucs] for cat, name, used in SOURCES]
    colfmt = {c: {"rowColors": '> table | seriesByName("%s") | matchValue(dot)' % c,
                  "rowBackgroundColors": '> table | seriesByName("%s") | matchValue(dotBg)' % c,
                  "align": "center", "headerAlign": "center", "width": 96} for _, c in ucs}
    colfmt["Data source"] = {"width": 300}
    colfmt["Category"] = {"width": 190, "rowColors": '> table | seriesByName("Category") | matchValue(muted)'}
    muted = [{"match": cat, "value": INK_2} for cat in sorted({c for c, _, _ in SOURCES})]
    mh = 70 + 30 * (len(SOURCES) + 1)
    d.card(M, y, INNER, mh, "splunk.table", tbl(cols, rows),
           title="Data sources needed in Splunk \u2014 and the use cases each one serves",
           options={"headerVisibility": "fixed", "fontSize": "small", "count": len(SOURCES), "columnFormat": colfmt},
           context={"dot": [{"match": "\u2713", "value": "#5FE3EE"}], "dotBg": [{"match": "\u2713", "value": "#12343A"}],
                    "muted": muted})
    return d


BUILDERS = [overview, uc1, uc2, uc3, uc4, uc5, uc6, uc7, uc8, uc9]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", help="also write ds.test JSON definitions to this directory")
    ap.add_argument("--only", help="build only this view")
    a = ap.parse_args()
    now = dt.datetime.now().replace(second=0, microsecond=0)
    for b in BUILDERS:
        d = b()
        if a.only and d.view != a.only:
            continue
        with open(os.path.join(VIEWS_DIR, d.view + ".xml"), "w", encoding="utf-8") as f:
            f.write(d.xml(now))
        if a.preview:
            os.makedirs(a.preview, exist_ok=True)
            with open(os.path.join(a.preview, d.view + ".json"), "w", encoding="utf-8") as f:
                json.dump(d.definition("preview", now), f, ensure_ascii=False)
        print("wrote", d.view)


if __name__ == "__main__":
    main()
