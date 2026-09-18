#!/usr/bin/env python3
# ===========================================================================
# seed_glass_table.py - generate (and optionally seed) THREE native ITSI
# Glass Tables (GTv2 / Dashboard-Studio schema) for the dCloud lab:
#
#   1) dCloud - NOC Ops Wall      glass_tables/glass_table_noc.json
#   2) dCloud - Service Topology  glass_tables/glass_table_topology.json
#   3) dCloud - Business Services glass_tables/glass_table_exec.json
#
# All three read the SAME host-keyed live signals the ITSI KPIs use
# (index=berlin_web sourcetype=port:probe ... / index=*_metrics
# sourcetype=linux:metrics ...), so they render correctly with no service
# _keys and survive every re-seed.
#
# ---------------------------------------------------------------------------
# TWO WAYS TO USE
#
#   A) IMPORT (recommended, proven path):
#        sudo -u splunk /opt/splunk/bin/splunk cmd python3 \
#          /opt/dcloud-splunk/itsi/seed_glass_table.py --write
#      then in ITSI: Dashboards/Glass Tables -> Create Glass Table ->
#      (Source / </>) -> paste the contents of the matching JSON file.
#      --write always runs; the files are the source of truth.
#
#   B) SEED via API (best-effort, reset-proof) - also POST them:
#        sudo -u splunk /opt/splunk/bin/splunk cmd python3 \
#          /opt/dcloud-splunk/itsi/seed_glass_table.py \
#          --user admin --password C1sco12345 --seed --verbose
#      Idempotent per title. The GTv2 API schema shifts between ITSI
#      versions; if a POST is rejected, --write still gave you the files to
#      import by hand (path A), which always works.
# ===========================================================================
import argparse
import base64
import json
import os
import ssl
import sys
from urllib import request, parse, error

APP_NS = "servicesNS/nobody/itsi"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "glass_tables")

# --- palette --------------------------------------------------------------
GREEN, RED, AMBER = "#2E8B4F", "#B23B3B", "#C9821B"
DARK = "#0b0e13"          # canvas
PANEL = "#151d2c"         # tile / card
PANEL_2 = "#1b2536"
LINE = "#33445e"          # topology connectors
LIME = "#65a637"          # splunk chevron
CYAN = "#6bd1ff"
INK = "#e8edf5"
DIM = "#9fb0c6"

# color contexts (v is the numeric driver; higher=better for reachability)
REACH_BG = [{"to": 50, "value": RED}, {"from": 50, "value": GREEN}]      # 0 down -> red, 100 up -> green
CPU_BG = [{"to": 70, "value": GREEN}, {"from": 70, "to": 90, "value": AMBER}, {"from": 90, "value": RED}]

E = "earliest=-15m"

# --- device-type icons (emoji render fine in singlevalue/markdown) --------
IC = {"global": "\U0001F310", "site": "\U0001F4CD", "app": "\U0001F310",
      "db": "\U0001F5C4️", "hv": "\U0001F9F1", "srv": "\U0001F5A5️",
      "net": "\U0001F500", "splunk": "▸"}

# Wordmark URL shown on each table, rendered as a Markdown image (see mark_img).
# A styled <div> gets sanitized (style stripped) and a splunk.image viz is rejected
# by the glass_table endpoint (HTTP 500); ![](url) inside splunk.markdown works.
# raw.githubusercontent.com serves .svg as image/svg+xml with CORS *, so the SVG
# in itsi/glass_tables/assets/ loads directly. Override with GLASS_MARK_URL.
MARK_URL = os.environ.get(
    "GLASS_MARK_URL",
    "https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/itsi/glass_tables/assets/splunk_mark.svg")


# ---------------------------------------------------------------------------
# Shared data sources - the live signal behind every tile / node / card.
# ---------------------------------------------------------------------------
def _ds(query, name):
    return {"type": "ds.search",
            "options": {"query": query,
                        "queryParameters": {"earliest": "-15m", "latest": "now"}},
            "name": name}


def _reach_host(idx, host):
    return ("index=%s sourcetype=port:probe host=%s | stats count as c latest(open) as o "
            "| eval v=if(c>0 AND o=1,100,0), status=if(v>=50,\"UP\",\"DOWN\") | table status v"
            % (idx, host))


def _hb_host(idx, host):
    return ("index=%s sourcetype=linux:metrics host=%s | stats count as c "
            "| eval v=if(c>0,100,0), status=if(v>=50,\"UP\",\"DOWN\") | table status v"
            % (idx, host))


def _rollup(parts):
    # parts: list of (fieldname, subsearch producing that 0/1 field). ANDs them.
    cols = "".join(" \n| appendcols [ %s ]" % s for _, s in parts)
    ands = " AND ".join("%s=1" % f for f, _ in parts)
    return ("| makeresults%s \n| eval v=if(%s,100,0), status=if(v>=50,\"UP\",\"DOWN\") "
            "| table status v" % (cols, ands))


def _reach_flag(f, idx, host):
    return (f, "search index=%s sourcetype=port:probe host=%s %s | stats count as c latest(open) as o "
               "| eval %s=if(c>0 AND o=1,1,0) | fields %s" % (idx, host, E, f, f))


def _hb_flag(f, idx, host):
    return (f, "search index=%s sourcetype=linux:metrics host=%s %s | stats count as c "
               "| eval %s=if(c>0,1,0) | fields %s" % (idx, host, E, f, f))


def _cpu(idx, host):
    return ("index=%s sourcetype=linux:metrics host=%s | stats latest(cpu_pct) as raw "
            "| eval v=coalesce(raw,0), status=round(v).\"%%\" | table status v" % (idx, host))


def data_sources():
    ds = {}
    # leaves
    ds["ds_web"] = _ds(_reach_host("berlin_web", "webapp-berlin"), "Web Service")
    ds["ds_db"] = _ds(_reach_host("berlin_web", "db-berlin"), "Database Service")
    ds["ds_proxmox"] = _ds(_reach_host("berlin_web", "proxmox-berlin"), "Proxmox Hypervisor")
    ds["ds_ubuntu_b"] = _ds(_hb_host("berlin_metrics", "ubuntu-berlin"), "Ubuntu Berlin")
    ds["ds_router_b"] = _ds(_reach_host("berlin_web", "cat8kv-berlin"), "Router Berlin")
    ds["ds_ubuntu_l"] = _ds(_hb_host("london_metrics", "ubuntu-london"), "Ubuntu London")
    ds["ds_router_l"] = _ds(_reach_host("london_web", "cat8kv-london"), "Router London")
    ds["ds_splunk"] = _ds("index=loc1_metrics sourcetype=linux:metrics | stats count as c "
                          "| eval v=if(c>0,100,0), status=if(v>=50,\"UP\",\"DOWN\") | table status v",
                          "Splunk Core")
    # cpu (for exec KPI rows)
    ds["ds_cpu_ub_b"] = _ds(_cpu("berlin_metrics", "ubuntu-berlin"), "Ubuntu Berlin CPU")
    ds["ds_cpu_ub_l"] = _ds(_cpu("london_metrics", "ubuntu-london"), "Ubuntu London CPU")
    # rollups
    ds["ds_dirapp"] = _ds(_rollup([_reach_flag("f0", "berlin_web", "webapp-berlin"),
                                   _reach_flag("f1", "berlin_web", "db-berlin")]), "Directory App")
    ds["ds_berlin_infra"] = _ds(_rollup([_reach_flag("f0", "berlin_web", "proxmox-berlin"),
                                         _hb_flag("f1", "berlin_metrics", "ubuntu-berlin"),
                                         _reach_flag("f2", "berlin_web", "cat8kv-berlin")]),
                                "Berlin Infrastructure")
    ds["ds_berlin"] = _ds(_rollup([_reach_flag("f0", "berlin_web", "webapp-berlin"),
                                   _reach_flag("f1", "berlin_web", "db-berlin"),
                                   _reach_flag("f2", "berlin_web", "proxmox-berlin"),
                                   _hb_flag("f3", "berlin_metrics", "ubuntu-berlin"),
                                   _reach_flag("f4", "berlin_web", "cat8kv-berlin")]), "Berlin")
    ds["ds_london"] = _ds(_rollup([_hb_flag("f0", "london_metrics", "ubuntu-london"),
                                   _reach_flag("f1", "london_web", "cat8kv-london")]), "London")
    ds["ds_loc1"] = _ds("index=loc1_metrics sourcetype=linux:metrics | stats count as c "
                        "| eval v=if(c>0,100,0), status=if(v>=50,\"UP\",\"DOWN\") | table status v",
                        "Location 1")
    ds["ds_global"] = _ds(_rollup([_reach_flag("f0", "berlin_web", "webapp-berlin"),
                                   _reach_flag("f1", "berlin_web", "db-berlin"),
                                   _reach_flag("f2", "berlin_web", "proxmox-berlin"),
                                   _hb_flag("f3", "berlin_metrics", "ubuntu-berlin"),
                                   _hb_flag("f4", "london_metrics", "ubuntu-london")]),
                          "Global IT Operations")
    return ds


# ---------------------------------------------------------------------------
# viz helpers
# ---------------------------------------------------------------------------
def sv(under, ds_id, ctx_vals, series="status", font=40, ctx="bg", unit=None):
    o = {"majorValue": "> primary | seriesByName('%s')" % series,
         "backgroundColor": "> primary | seriesByName('v') | rangeValue(%s)" % ctx,
         "majorColor": "#ffffff", "majorFontSize": font, "sparklineDisplay": "off",
         "numberPrecision": 0, "underLabel": under, "unitPosition": "after"}
    if unit:
        o["unit"] = unit
    return {"type": "splunk.singlevalue", "options": o,
            "context": {ctx: ctx_vals}, "dataSources": {"primary": ds_id}, "title": under}


def md(text):
    return {"type": "splunk.markdown", "options": {"markdown": text}}


def mark_img(src):
    # Wordmark as a Markdown image inside a splunk.markdown viz. The ITSI
    # glass_table endpoint rejects a splunk.image viz (HTTP 500), but the
    # markdown viz round-trips fine and renders ![](url) as an <img>.
    return md("![splunk](%s)" % src)


def rect(fill=PANEL, opacity=1.0, stroke=None, sw=0, rounding=0):
    o = {"fillColor": fill, "fillOpacity": opacity,
         "strokeColor": stroke or fill, "strokeWidth": sw}
    if rounding:
        o["rounding"] = rounding
    return {"type": "splunk.rectangle", "options": o}


def pos(item, x, y, w, h):
    return {"item": item, "type": "block", "position": {"x": x, "y": y, "w": w, "h": h}}


INPUTS = {
    "input_global_trp": {"options": {"defaultValue": "-60m@m, now", "token": "global_time"},
                         "type": "input.timerange", "title": "Global Time Range"},
    "input_global_refresh_rate": {
        "options": {"items": [{"value": "60s", "label": "1 Minute"},
                              {"value": "300s", "label": "5 Minutes"},
                              {"value": "1800s", "label": "30 Minutes"}],
                    "defaultValue": "60s", "token": "global_refresh_rate"},
        "type": "input.dropdown", "title": "Global Refresh Rate"},
}

DEFAULTS = {"dataSources": {"global": {"options": {
    "queryParameters": {"earliest": "$global_time.earliest$", "latest": "$global_time.latest$"},
    "refreshType": "delay", "refresh": "$global_refresh_rate$"}}}}


def wrap(title, description, width, height, structure, viz, ds, label="View"):
    return {
        "title": title, "description": description, "defaults": DEFAULTS,
        "layout": {
            "options": {"showTitleAndDescription": True},
            "globalInputs": ["input_global_trp", "input_global_refresh_rate"],
            "tabs": {"items": [{"layoutId": "layout_1", "label": label}]},
            "layoutDefinitions": {"layout_1": {"type": "absolute",
                                               "options": {"width": width, "height": height},
                                               "structure": structure}},
        },
        "dataSources": ds, "visualizations": viz, "inputs": INPUTS,
    }


# ===========================================================================
# STYLE 1 - NOC OPS WALL
# ===========================================================================
def build_noc():
    ds = data_sources()
    viz, st = {}, []
    W, H = 1920, 1000

    viz["bg_canvas"] = rect(DARK, 1.0)
    st.append(pos("bg_canvas", 0, 0, W, H))

    viz["hdr"] = md("<div style='font:800 26px Inter,sans-serif;color:%s'>GLOBAL IT OPERATIONS</div>"
                    "<div style='font:600 13px monospace;color:%s;letter-spacing:.14em'>SERVICE HEALTH WALL &middot; refresh 60s</div>" % (INK, DIM))
    st.append(pos("hdr", 60, 40, 900, 90))
    viz["mark"] = mark_img(MARK_URL)
    st.append(pos("mark", 1420, 55, 440, 60))

    # hero global score
    viz["viz_global"] = sv("GLOBAL SERVICE HEALTH", "ds_global", REACH_BG, font=56)
    st.append(pos("viz_global", 640, 150, 640, 150))

    # zones + tiles
    zones = [
        ("Berlin", "BERLIN — Location 3", "#12303a", "#1f6f8b", 60, 340, 900, 600,
         [("Directory App", "ds_dirapp", IC["app"], REACH_BG),
          ("Web Service", "ds_web", IC["app"], REACH_BG),
          ("Database Service", "ds_db", IC["db"], REACH_BG),
          ("Proxmox Hypervisor", "ds_proxmox", IC["hv"], REACH_BG),
          ("Ubuntu Berlin", "ds_ubuntu_b", IC["srv"], REACH_BG),
          ("Router Berlin", "ds_router_b", IC["net"], REACH_BG)], 2),
        ("London", "LONDON — Location 2", "#0e2f33", "#1f8b7a", 1000, 340, 430, 600,
         [("Ubuntu London", "ds_ubuntu_l", IC["srv"], REACH_BG),
          ("Router London", "ds_router_l", IC["net"], REACH_BG)], 1),
        ("Loc1", "LOCATION 1 — Core", "#2a2438", "#7a5cff", 1470, 340, 390, 600,
         [("Splunk Core", "ds_splunk", IC["splunk"], REACH_BG)], 1),
    ]
    for zid, zlabel, zfill, zstroke, zx, zy, zw, zh, tiles, cols in zones:
        viz["zone_%s" % zid] = rect(zfill, 0.35, zstroke, 2, 14)
        st.append(pos("zone_%s" % zid, zx, zy, zw, zh))
        viz["lbl_%s" % zid] = md("<div style='font:700 15px monospace;color:%s;letter-spacing:.12em'>%s</div>" % (DIM, zlabel))
        st.append(pos("lbl_%s" % zid, zx + 20, zy + 14, zw - 40, 30))
        pad, gap, ty0 = 24, 20, zy + 60
        tw = (zw - 2 * pad - (cols - 1) * gap) // cols
        thh = 150
        for i, (name, dsid, icon, ctx) in enumerate(tiles):
            r, c = divmod(i, cols)
            tx = zx + pad + c * (tw + gap)
            ty = ty0 + r * (thh + gap)
            vid = "viz_noc_%s" % dsid
            viz[vid] = sv("%s %s" % (icon, name), dsid, ctx, font=34)
            st.append(pos(vid, tx, ty, tw, thh))

    return wrap("dCloud - NOC Ops Wall",
                "Live NOC wall - green = reachable, red = down. Same signals as the ITSI service tree.",
                W, H, st, viz, ds, label="Ops Wall")


# ===========================================================================
# STYLE 2 - NETWORK TOPOLOGY (draw.io style)
# ===========================================================================
def _connect(viz, st, cid, p, c, midy):
    """Orthogonal L-connector from parent-bottom-center p=(cx,by) to
    child-top-center c=(cx,ty), routed through horizontal band midy."""
    pcx, pby = p
    ccx, cty = c
    segs = [(pcx - 1, pby, 3, midy - pby),
            (min(pcx, ccx) - 1, midy - 1, abs(ccx - pcx) + 3, 3),
            (ccx - 1, midy, 3, cty - midy)]
    for i, (x, y, w, h) in enumerate(segs):
        if w <= 0 or h <= 0:
            continue
        vid = "%s_%d" % (cid, i)
        viz[vid] = rect(LINE, 1.0, LINE, 0)
        st.append(pos(vid, x, y, w, h))


def _node(viz, st, nid, label, dsid, ctx, x, y, w, h, font=22):
    viz[nid] = sv(label, dsid, ctx, font=font)
    st.append(pos(nid, x, y, w, h))


def build_topology():
    ds = data_sources()
    viz, st = {}, []
    W, H = 1920, 820

    # dark canvas + a couple of faint band rects for depth (image-free, reset-proof)
    viz["bg_canvas"] = rect("#0a0e16", 1.0)
    st.append(pos("bg_canvas", 0, 0, W, H))
    # tier bands (very subtle) to read as a layered diagram
    for i, (by, bh) in enumerate([(230, 110), (460, 110)]):
        vid = "band_%d" % i
        viz[vid] = rect("#10192a", 0.45, "#10192a", 0)
        st.append(pos(vid, 0, by, W, bh))

    # node geometry: id, label(with icon), ds, ctx, cx, y, w, h
    NW, NH = 210, 84
    def N(cx, y, w=NW, h=NH):
        return dict(cx=cx, y=y, w=w, h=h)

    nodes = {
        "global":  ("%s Global IT Ops" % IC["global"], "ds_global", REACH_BG, N(960, 40, 300, 92)),
        "berlin":  ("%s Berlin" % IC["site"], "ds_berlin", REACH_BG, N(467, 240, 260)),
        "london":  ("%s London" % IC["site"], "ds_london", REACH_BG, N(1100, 240, 240)),
        "loc1":    ("%s Location 1" % IC["site"], "ds_loc1", REACH_BG, N(1590, 240, 260)),
        "dirapp":  ("%s Directory App" % IC["app"], "ds_dirapp", REACH_BG, N(165, 470, 180)),
        "proxmox": ("%s Proxmox" % IC["hv"], "ds_proxmox", REACH_BG, N(380, 470, 175)),
        "ubuntu_b":("%s Ubuntu Berlin" % IC["srv"], "ds_ubuntu_b", REACH_BG, N(575, 470, 185)),
        "router_b":("%s Router Berlin" % IC["net"], "ds_router_b", REACH_BG, N(770, 470, 175)),
        "web":     ("%s Web" % IC["app"], "ds_web", REACH_BG, N(80, 660, 150, 76)),
        "db":      ("%s Database" % IC["db"], "ds_db", REACH_BG, N(250, 660, 150, 76)),
        "ubuntu_l":("%s Ubuntu London" % IC["srv"], "ds_ubuntu_l", REACH_BG, N(1000, 470, 180)),
        "router_l":("%s Router London" % IC["net"], "ds_router_l", REACH_BG, N(1200, 470, 180)),
        "splunk":  ("%s Splunk Core" % IC["splunk"], "ds_splunk", REACH_BG, N(1590, 470, 220)),
    }
    edges = [  # (parent, child, midy)
        ("global", "berlin", 190), ("global", "london", 190), ("global", "loc1", 190),
        ("berlin", "dirapp", 420), ("berlin", "proxmox", 420),
        ("berlin", "ubuntu_b", 420), ("berlin", "router_b", 420),
        ("dirapp", "web", 620), ("dirapp", "db", 620),
        ("london", "ubuntu_l", 420), ("london", "router_l", 420),
        ("loc1", "splunk", 420),
    ]

    def geom(k):
        g = nodes[k][3]
        return g["cx"], g["y"], g["w"], g["h"]

    # connectors first (drawn under nodes)
    for i, (pk, ck, midy) in enumerate(edges):
        pcx, py, pw, ph = geom(pk)
        ccx, cy, cw, ch = geom(ck)
        _connect(viz, st, "conn_%d" % i, (pcx, py + ph), (ccx, cy), midy)

    # nodes on top
    for k, (label, dsid, ctx, g) in nodes.items():
        font = 26 if k == "global" else (20 if g["w"] < 170 else 22)
        _node(viz, st, "viz_%s" % k, label, dsid, ctx,
              g["cx"] - g["w"] // 2, g["y"], g["w"], g["h"], font=font)

    # splunk watermark (image, not markdown - see MARK_URL note)
    viz["mark"] = mark_img(MARK_URL)
    st.append(pos("mark", 40, 760, 300, 40))

    return wrap("dCloud - Service Topology",
                "Service dependency map - node color is live reachability; edges show blast radius.",
                W, H, st, viz, ds, label="Topology")


# ===========================================================================
# STYLE 3 - EXECUTIVE / BUSINESS SERVICE CARDS
# ===========================================================================
def build_exec():
    ds = data_sources()
    viz, st = {}, []
    W, H = 1920, 900

    viz["bg_canvas"] = rect("#0e1420", 1.0)
    st.append(pos("bg_canvas", 0, 0, W, H))

    viz["hdr"] = md("<div style='font:800 24px Inter,sans-serif;color:%s'>Business Service Overview</div>"
                    "<div style='font:500 14px Inter,sans-serif;color:%s;margin-top:6px'>Berlin &middot; London &middot; Location 1 &mdash; live KPI rollup</div>" % (INK, DIM))
    st.append(pos("hdr", 60, 44, 1000, 100))
    viz["mark"] = mark_img(MARK_URL)
    st.append(pos("mark", 1420, 55, 440, 60))

    # global health hero (ring-like rounded tile)
    viz["hero_ring"] = rect(PANEL_2, 1.0, "#2ec26a", 3, 999)
    st.append(pos("hero_ring", 1600, 130, 240, 150))
    viz["viz_hero"] = sv("GLOBAL HEALTH", "ds_global", REACH_BG, font=52)
    st.append(pos("viz_hero", 1610, 140, 220, 130))

    # cards: (title, icon, sublabel, status_ds, [ (kpi_label, ds, ctx) ... ])
    cards = [
        ("Directory App", IC["app"], "Berlin · customer-facing", "ds_dirapp",
         [("Web reachability", "ds_web", REACH_BG),
          ("Database reachability", "ds_db", REACH_BG)]),
        ("Berlin Infrastructure", IC["hv"], "Location 3", "ds_berlin_infra",
         [("Proxmox", "ds_proxmox", REACH_BG),
          ("Ubuntu Berlin", "ds_ubuntu_b", REACH_BG),
          ("CPU load", "ds_cpu_ub_b", CPU_BG)]),
        ("London Infrastructure", IC["srv"], "Location 2", "ds_london",
         [("Ubuntu London", "ds_ubuntu_l", REACH_BG),
          ("Router London", "ds_router_l", REACH_BG),
          ("CPU load", "ds_cpu_ub_l", CPU_BG)]),
        ("Splunk Core", IC["splunk"], "Location 1", "ds_loc1",
         [("Reachability", "ds_splunk", REACH_BG)]),
    ]

    n = len(cards)
    margin, gap = 60, 30
    cw = (W - 2 * margin - (n - 1) * gap) // n
    cy, chh = 320, 500
    for i, (title, icon, sub, sds, kpis) in enumerate(cards):
        cx = margin + i * (cw + gap)
        cid = "card_%d" % i
        viz[cid] = rect(PANEL, 1.0, LINE, 1, 16)
        st.append(pos(cid, cx, cy, cw, chh))
        # header (icon + title + sub)
        viz["%s_h" % cid] = md(
            "<div style='font:800 26px Inter,sans-serif;color:%s'>%s</div>"
            "<div style='font:700 17px Inter,sans-serif;color:%s;margin-top:6px'>%s</div>"
            "<div style='font:500 12px monospace;color:%s;margin-top:2px'>%s</div>"
            % (INK, icon, INK, title, DIM, sub))
        st.append(pos("%s_h" % cid, cx + 22, cy + 20, cw - 44, 110))
        # status badge (big)
        viz["%s_badge" % cid] = sv("STATUS", sds, REACH_BG, font=30)
        st.append(pos("%s_badge" % cid, cx + 22, cy + 140, cw - 44, 110))
        # kpi rows
        ky = cy + 270
        kh = 66
        for j, (klabel, kds, kctx) in enumerate(kpis):
            vid = "%s_kpi_%d" % (cid, j)
            viz[vid] = sv(klabel, kds, kctx, font=22)
            st.append(pos(vid, cx + 22, ky + j * (kh + 8), cw - 44, kh))

    return wrap("dCloud - Business Services",
                "Executive view - branded service cards with live KPI breakdown per business service.",
                W, H, st, viz, ds, label="Services")


BUILDERS = {
    "glass_table_noc.json": build_noc,
    "glass_table_topology.json": build_topology,
    "glass_table_exec.json": build_exec,
}


# ---------------------------------------------------------------------------
# ITSI API client (best-effort seeding)
# ---------------------------------------------------------------------------
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


def seed_one(itsi, definition, owner="nobody"):
    title = definition["title"]
    payload = {"title": title, "description": definition["description"],
               "gt_version": "beta", "_owner": owner, "_user": owner,
               "acl": {"sharing": "global"}, "definition": definition}
    code, res = itsi.call("GET", "%s/itoa_interface/glass_table" % APP_NS,
                          params={"filter": json.dumps({"title": title}), "fields": "_key,title"})
    if code != 200:
        return code, "cannot reach itoa_interface/glass_table"
    key = res[0]["_key"] if isinstance(res, list) and res else None
    if key:
        # The UPDATE handler reads `owner` from the REQUEST params (not the data
        # body); without it, itoa_interface 500s with {"message":"'owner'"}. Send
        # it as a POST form field alongside the JSON `data` payload.
        return itsi.call("POST", "%s/itoa_interface/glass_table/%s" % (APP_NS, key),
                         body={"data": json.dumps(dict(payload, _key=key)), "owner": owner})
    return itsi.call("POST", "%s/itoa_interface/glass_table" % APP_NS,
                     body={"data": json.dumps(payload)})


def main():
    ap = argparse.ArgumentParser(description="Generate/seed the three dCloud ITSI Glass Tables.")
    ap.add_argument("--host", default=os.environ.get("SPLUNK_MGMT", "https://localhost:8089"))
    ap.add_argument("--user", default=os.environ.get("SPLUNK_ADMIN_USER", "admin"))
    ap.add_argument("--password", default=os.environ.get("SPLUNK_ADMIN_PASSWORD", "C1sco12345"))
    ap.add_argument("--write", action="store_true", help="(default action) write the JSON files to itsi/glass_tables/")
    ap.add_argument("--seed", action="store_true", help="also POST each definition to the ITSI API")
    ap.add_argument("--owner", default="nobody")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    # --write is the default; emit the files when the checkout is writable.
    # Seeding only needs the in-memory definitions, so a read-only checkout
    # (e.g. run as the splunk user against a root-owned /opt/dcloud-splunk clone)
    # is a warning, not a failure.
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
    except OSError:
        pass
    defs = {}
    wrote_any = False
    for fname, builder in BUILDERS.items():
        d = builder()
        defs[fname] = d
        path = os.path.join(OUT_DIR, fname)
        try:
            with open(path, "w") as fh:
                json.dump(d, fh, indent=2)
            wrote_any = True
            print("wrote %s  (%s)" % (path, d["title"]))
        except OSError as exc:
            sys.stderr.write("note: could not write %s (%s) - continuing with the "
                             "in-memory definition\n" % (path, exc))

    if not args.seed:
        if not wrote_any:
            sys.stderr.write("Nothing written (read-only checkout) and --seed not given. "
                             "Re-run with --seed to POST via the API, or make %s writable "
                             "(chown to the splunk user) to regenerate the files.\n" % OUT_DIR)
            return 1
        print("\nImport: ITSI -> Glass Tables -> Create -> Source (</>) -> paste a file's contents.")
        print("Or re-run with --user/--password --seed to POST them via the API.")
        return 0

    itsi = ITSI(args.host, args.user, args.password, verbose=args.verbose)
    rc = 0
    for fname, d in defs.items():
        code, res = seed_one(itsi, d, owner=args.owner)
        if code in (200, 201):
            print("seeded: '%s'" % d["title"])
        else:
            rc = 1
            sys.stderr.write("FAILED to seed '%s' (HTTP %s): %s\n"
                             "  -> the files from --write still import by hand.\n"
                             % (d["title"], code, str(res)[:700]))
    return rc


if __name__ == "__main__":
    sys.exit(main())
