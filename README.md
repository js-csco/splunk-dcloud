# splunk-dcloud

> **A self-rebuilding Splunk demo lab for Cisco dCloud.** The VM resets to an empty
> template each session; one startup command pulls this repo and rebuilds the entire
> Splunk configuration from code — per-location indexes, RBAC, users, dashboards, and
> data inputs. Nothing is configured by hand, so the lab is **identical every time**.

To change the lab, edit files here and `git push` to `main` — the next session picks
it up automatically. The dCloud startup command never changes.

<img src="splunk/apps/dcloud_lab/appserver/static/topology.svg" alt="Lab topology" width="640">

### What's inside

- **Location-based RBAC** — one index namespace per location (`loc1_*`, `london_*`,
  `berlin_*`); roles grant access per location; three demo users.
- **Every ingestion method** — Universal Forwarders (Linux servers + a desktop client),
  scripted REST polling (Proxmox), SSH polling (Cisco routers), SC4SNMP, file monitors.
- **Use cases you can demo live** — host/infra metrics, alerts, asset inventory +
  enrichment, a data model + Pivot, a **Client → Hypervisor → App** correlation, a
  **Splunk MCP Server** for Claude Desktop, and an ITSI path (episodes + glass tables).

### Hosts at a glance

| Host | Location | Address | Feeds Splunk |
|---|---|---|---|
| splunk | loc1 | 198.18.1.124 | `loc1_*`; polls routers + Proxmox |
| ubuntu-london | London | 198.18.2.x | UF → `london_linux`, `london_metrics` |
| ubuntu-desktop-london (client) | London | 198.18.2.11 | UF → `london_metrics`, `london_linux` |
| cat8kv-london (router) | London | 198.18.2.32 | SSH poll → `london_network` |
| proxmox-berlin (hypervisor) | Berlin | 198.18.3.11 | API poll → `berlin_proxmox`, `berlin_metrics` |
| webapp-berlin (LXC) | Berlin | 198.18.3.50:8080 | UF → `berlin_web`, `berlin_metrics` |
| db-berlin (PostgreSQL LXC) | Berlin | 198.18.3.51:5432 | UF → `berlin_db`, `berlin_metrics` |
| ubuntu-berlin-snmp (SC4SNMP) | Berlin | 198.18.3.52 | SC4SNMP → HEC → `berlin_snmp` |
| ubuntu-berlin | Berlin | 198.18.3.x | UF → `berlin_linux`, `berlin_metrics` |
| cat8kv-berlin (router) | Berlin | 198.18.3.32 | SSH poll → `berlin_network` |

Demo logins (all password `C1sco12345`): `admin` · `gary` (global) · `leo` (London) · `ben` (Berlin).

---

## Contents

- **Setup:** [How it works](#how-it-works) · [Startup (each session)](#startup-each-session) · [Cisco routers](#cisco-routers-console-bring-up) · [Troubleshooting: DNS](#troubleshooting-dns)
- **Core design:** [Location-based RBAC](#location-based-rbac-the-core-design)
- **Use cases (demos):** [Dashboards](#dashboards-lab-overview-app) · [Host & infra metrics](#host--infrastructure-metrics) · [Alerts & ITSI](#alerts) · [Asset Configuration](#asset-configuration-inventory--enrichment) · [Client → Hypervisor → App](#client--hypervisor--app-correlation) · [Splunk MCP Server](#splunk-mcp-server-claude-desktop) · [Save to GitHub](#save-to-github-persisting-demo-changes)
- **Reference:** [Get Data In](#get-data-in-ingestion-methods) · [SNMP (SC4SNMP)](#snmp-via-splunk-connect-for-snmp-sc4snmp) · [Install ITSI / ITE-W](#install-itsi--it-essentials-work) · [Repo layout](#repo-layout) · [Environment](#environment-assumptions) · [Roadmap](#roadmap)

---

## How it works

```
dCloud Startup Automation (session.xml)
        │  one command  (fixes DNS, ensures git, clones -b main)
        ▼
   apply.sh          idempotent orchestrator
        ├── deploys the dcloud_lab app (indexes + dashboards + static assets)
        ├── starts/restarts Splunk (via systemd) so indexes take effect
        ├── creates RBAC roles at runtime via REST
        ├── creates users via the Splunk CLI (mapped to roles)
        └── data inputs (HEC / forwarder inputs for Ubuntu + Proxmox)
```

`bootstrap.sh` is an alternative entry point that clones/refreshes an existing
checkout and hands off to `apply.sh`; the canonical startup command below runs
`apply.sh` directly.

## Startup (each session)

Everything resets each session. Run these in order.

**1. Splunk box** — build the whole config (indexes, roles, users, dashboards, SSH
router polling):

```bash
sudo rm -rf /opt/dcloud-splunk && sudo git clone -b main https://github.com/js-csco/splunk-dcloud.git /opt/dcloud-splunk && sudo bash /opt/dcloud-splunk/apply.sh
```

> The Splunk box collects **its own** host metrics automatically (`loc1_metrics`),
> so the **Host & Infra Metrics** dashboard has data before any UF is installed.

**2. ubuntu-london** — forward syslog, then install the Universal Forwarder (host
metrics → `london_metrics`, `/var/log` → `london_linux`):

```bash
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/forward-to-splunk.sh | sudo bash -s -- london
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/install-uf.sh        | sudo bash -s -- london
```

**3. ubuntu-berlin** — forward syslog, then install the UF (host metrics →
`berlin_metrics`, `/var/log` → `berlin_linux`, **and** the local Proxmox API →
`berlin_proxmox` + `berlin_metrics`):

```bash
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/forward-to-splunk.sh | sudo bash -s -- berlin
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/install-uf.sh        | sudo bash -s -- berlin
```

> **Proxmox polling** works out of the box — the demo creds (`root@pam` /
> `C1sco12345`, host `198.18.3.11`) ship in `TA-dcloud-proxmox/bin/proxmox_config.env`.
> `install-uf.sh berlin` also prompts to override the password (stored out-of-repo at
> `/opt/splunkforwarder/var/lib/dcloud/proxmox.env`); press Enter to keep the default.
> Verify the poller authenticates:
> ```bash
> sudo -u splunk /opt/splunkforwarder/bin/splunk cmd python3 \
>   /opt/splunkforwarder/etc/apps/TA-dcloud-proxmox/bin/poll_proxmox.py
> ```
> You want JSON with `cluster/resources` data, not `"status":"error"`.

**4. Linux desktop client (London)** — the end-user client in the **Client →
Hypervisor → App** correlation. Install the UF on the Ubuntu 24.04 desktop; it
forwards with **live timestamps**: host metrics → `london_metrics`, and logged-on
users / top processes / `/var/log` logins → `london_linux`. It gets the distinct host
id `desktop-london` so it doesn't collide with the infra box `ubuntu-london`:

```bash
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/install-uf-desktop.sh | sudo bash
```

> **Logging in over RDP:** the desktop uses **xrdp/Xvnc**, so RDP lands on the xrdp
> login — it wants the **OS account**: session **Xorg** (or Xvnc), user **`cisco`**,
> password **`C1sco12345`**. A black screen after login usually means the desktop is
> already open on the console (GNOME allows one session) — log out of the console
> first, then reconnect.

**5. web-app container on Proxmox (Berlin)** — completes the **Client → Hypervisor →
App** correlation. Requires Proxmox reachable at `198.18.3.11` and step 3 done. Creates
an Ubuntu LXC (VMID 200) at `198.18.3.50` and provisions the web-app + a UF inside it
(access log → `berlin_web` `webapp:access`, host metrics → `berlin_metrics`, host
`webapp-berlin`). Run it on the **Proxmox host as root** (Debian — runs `pct` locally,
no SSH), **or** on ubuntu-berlin with `sudo` (SSHes to Proxmox):

```bash
# on the Proxmox host as root:
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/create-webapp-container.sh | bash
# or on ubuntu-berlin:
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/create-webapp-container.sh | sudo bash
```

**5b. database container (Berlin)** — the DB tier of the **Directory App**: a
PostgreSQL LXC (VMID 201) at `198.18.3.51` holding the `teams` + `employees` tables
(the org chart). Adding an employee in the web UI writes here (recording the client
`src_ip`). Postgres logs → `berlin_db`, metrics → `berlin_metrics` (host `db-berlin`).
Same two ways to run it:

```bash
# on the Proxmox host as root:
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/create-db-container.sh | bash
# or on ubuntu-berlin:
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/create-db-container.sh | sudo bash
```

> "App available" (and the **Service Health** rollup on *Client → Hypervisor → App*)
> is green only when **both** containers are running and the app+DB are reachable —
> stop either container in Proxmox to show it flip to red. The **first** run downloads
> an Ubuntu LXC template on Proxmox (`pveam`), so Proxmox needs outbound internet.

Then generate a little traffic so the App panels fill (from the desktop or anywhere on
the lab network):

```bash
curl http://198.18.3.50:8080/
```

**6. (optional) demo data** — correlated events on the Ubuntu boxes (shared services
and users so you can correlate by **service** or **user**):

```bash
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/generate-activity.sh | bash
```

**7. (optional) SNMP and ITSI** — see [SNMP via SC4SNMP](#snmp-via-splunk-connect-for-snmp-sc4snmp)
and [Install ITSI / ITE-W](#install-itsi--it-essentials-work) in Reference.

Finally, open `http://198.18.1.124:8000` → **Lab Overview → Setup Status** (all green)
and log in as `leo` / `ben` / `gary` (password `C1sco12345`).

### Cisco routers (console bring-up)

The Catalyst 8000V routers ship **empty** and are reachable **only via console**
(dCloud's web/VM console). They can't be SSH-polled until they have an IP + login, so
paste a baseline config once per session. The configs are versioned in [`routers/`](routers/):

- London → [`routers/london-cat8kv.txt`](routers/london-cat8kv.txt) (198.18.2.32)
- Berlin → [`routers/berlin-cat8kv.txt`](routers/berlin-cat8kv.txt) (198.18.3.32)

Steps at the console:
1. If asked *"enter initial configuration dialog?"* answer **no**.
2. Confirm the connected interface: `show ip interface brief` (the config assumes
   **GigabitEthernet1** — edit the file if yours differs).
3. Paste the whole file. It sets hostname, the LAN IP, `username cisco/cisco`
   (priv 15), a default route to the subnet gateway (`.1`), **SSH v2** (generates the
   RSA key), syslog to the Splunk box, and saves with `write memory`.
4. Verify from the Splunk box: `ssh cisco@198.18.2.32 "show version"`.

Each baseline gives the router what `ssh_router.sh` needs (creds in
`splunk/apps/get_data_in/bin/routers.csv`); the poller then pulls `show` output every
5 min into `*_network`. Nothing to install on the router — it's plain IOS config.

### Troubleshooting: DNS

If a box can't resolve `github.com` (`ping 1.1.1.1` works but `ping google.com` fails),
fix the resolver, then re-run:

```bash
sudo rm -f /etc/resolv.conf
printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\noptions timeout:2 attempts:2\n' | sudo tee /etc/resolv.conf
```

> Always clone with `-b main` (a plain `git clone` may pull an older default branch).

---

## Location-based RBAC (the core design)

Access control is enforced at the **index** level. The golden rule: an index belongs to
exactly ONE location, so a role granting one location's indexes can never leak another's.
Indexes are named `loc<N>_<datatype>` so a role grants a whole location with a single
wildcard.

| Location | Network | Devices | Indexes | Roles with access |
|---|---|---|---|---|
| loc1 | 198.18.1.0/24 | splunk (infrastructure) | `loc1_linux`, `loc1_metrics` | `role_global` only |
| London (loc2) | 198.18.2.0/24 | ubuntu-london, ubuntu-desktop-london, cat8kv-london | `london_linux`, `london_metrics`, `london_network` | `role_london`, `role_global` |
| Berlin (loc3) | 198.18.3.0/24 | proxmox-berlin, webapp-berlin, db-berlin, ubuntu-berlin, ubuntu-berlin-snmp, cat8kv-berlin | `berlin_linux`, `berlin_metrics`, `berlin_proxmox`, `berlin_network`, `berlin_web`, `berlin_db`, `berlin_snmp` | `role_berlin`, `role_global` |

**Roles** (created at runtime via REST in `apply.sh`): `role_london → london_*`,
`role_berlin → berlin_*`, `role_global → london_*;berlin_*;loc1_*` (+ `_*` for internal).
REST is used instead of `authorize.conf` because on this image app-level `authorize.conf`
roles did not register even after a restart, whereas REST creation is immediate.

> **Roles do NOT import the built-in `user` role.** On this image `user` grants
> `srchIndexesAllowed = *`, and Splunk *unions* inherited index access — so importing
> `user` would let a location role see every index. Instead each role gets explicit
> capabilities (search, rtsearch, …) and only its own indexes, keeping the wall airtight.
> The `*_metrics` indexes need no special handling — the location wildcards cover them.

**Users** (`config/lab_users.csv`, created at boot; passwords are throwaway lab creds —
don't put real ones here):

| User | Role | Sees |
|---|---|---|
| `gary` | `role_global` | everything (London + Berlin + loc1 infra) |
| `leo` | `role_london` | London only (`london_*`) |
| `ben` | `role_berlin` | Berlin only (`berlin_*`) |

---

# Use cases (demos)

The app ships a set of ready-to-show use cases. Each one is config-as-code and rebuilds
every session.

## Dashboards (Lab Overview app)

| Dashboard | Purpose |
|---|---|
| **Lab Info** (landing) | What the lab is, topology diagram, repo link, Splunk version, deploy command. |
| **Setup Status** | Post-deploy verification — green/red checklist confirming indexes, roles, users, and the app loaded. |
| **Ingestion & Health** | Event volume per location index and Splunk health. |
| **Save to GitHub** (admin) | Commit the current lab state to a new branch — see [Save to GitHub](#save-to-github-persisting-demo-changes). |

Plus the other apps: **Alerts**, **Infrastructure Monitoring** (*Data Onboarding
Overview*, *Host & Infra Metrics*, *Asset Configuration*), **Correlation**
(*Correlation 2/3 Sources*, *Client → Hypervisor → App*, *Root Cause Analysis*), and
**Get Data In** (ingestion methods, *Indexes and Sourcetypes*, *Data Model & Pivot*).

The **Correlation 2/3 Sources** views teach the canonical
`stats count(eval(source=A)) … by key` technique — pick sources and a correlation key
(service / user / host) and find the same entity across separate sources in a time
window, with the SPL shown on-screen and a plain-language explainer.

## Host & infrastructure metrics

The **Host & Infra Metrics** dashboard (Infrastructure Monitoring app) charts CPU /
memory / disk / load for every machine. A small agent samples the OS every 60s and emits
one `key=value` line per sample into per-location **event** indexes (`*_metrics`), charted
with `timechart`; Splunk's automatic `key=value` extraction turns `cpu_pct=…`,
`mem_used_pct=…`, etc. into fields with no extra config.

| Source | Collector | → Index | Notes |
|---|---|---|---|
| Splunk host (loc1) | local scripted input (`infra_monitoring`) | `loc1_metrics` | always on — no forwarder/network needed |
| ubuntu-london | UF `TA-dcloud-host` | `london_metrics` | after `install-uf.sh london` |
| desktop-london (client) | UF `TA-dcloud-host` (metrics + sessions + processes) | `london_metrics` + `london_linux` | after `install-uf-desktop.sh` |
| ubuntu-berlin | UF `TA-dcloud-host` | `berlin_metrics` | after `install-uf.sh berlin` |
| Proxmox (Berlin) | UF `TA-dcloud-proxmox` (`poll_proxmox.py metrics`) | `berlin_metrics` | per-node & per-guest CPU/mem/disk |

```spl
index=loc1_metrics OR index=london_metrics OR index=berlin_metrics sourcetype=linux:metrics | timechart span=1m avg(cpu_pct) by host
```

> **Why event indexes, not metric indexes?** Metric indexes + ingest-time
> log-to-metrics are elegant but fragile from a UF (a UF that does structured extraction
> forwards pre-cooked events that bypass the indexer's metric-schema transform, so the
> metric index silently drops them). Event indexes + `timechart` are reliable across the
> self-rebuilding lab and **directly searchable** — `index=london_metrics` shows the raw
> samples, so data arrival is trivial to confirm. RBAC is preserved: metrics land in
> per-location indexes, same wall as the logs.

**Proxmox metrics come from the same poll.** The Proxmox REST API already returns
per-node and per-guest CPU / memory / disk (from `/cluster/resources`). The UF poller
(`poll_proxmox.py metrics`, every 60s) emits those numbers as metric JSON into
`berlin_metrics` (sourcetype `proxmox:metrics`) — no metric-server or extra agent needed.

## Alerts

The **Alerts** app ships two scheduled alerts (`splunk/apps/alerts/default/savedsearches.conf`,
rebuilt every session). Both run every 5 minutes, trigger on `>0` results, email
**js-csco@proton.me**, and are tracked so they show under **Activity → Triggered Alerts**
and on the *Alerts* dashboard.

| Alert | Fires when | Search basis |
|---|---|---|
| **High CPU or Memory (>70%)** | any host's latest CPU% or mem% > 70% | `*_metrics` (`linux:metrics`) |
| **Universal Forwarder stopped sending** | a host sent metrics before but nothing for >10 min | `tstats latest(_time) by host` over `*_metrics` |

> **Email delivery:** the alert *logic* fires with no setup, but sending mail needs an
> SMTP relay Splunk can reach. The dCloud lab has none by default and Proton Mail doesn't
> accept arbitrary SMTP, so **email won't leave the lab until you configure a relay** —
> either in `alerts/default/alert_actions.conf` (`[email] mailserver = host:port`) or via
> **Settings → Server settings → Email settings**. The alert still fires and is visible
> in Splunk regardless.

**Trigger them for a demo** (dashboard panels refresh every 30s; or open the saved search
and click **Run**):

```bash
# CPU > 70% on any box — each worker self-stops after 240s (orphan-safe).
# Stop early with: pkill -x yes
for i in $(seq $(nproc)); do timeout 240 yes >/dev/null & done; wait

# Universal Forwarder stopped sending — stop the UF, then restart to clear
sudo /opt/splunkforwarder/bin/splunk stop
sudo /opt/splunkforwarder/bin/splunk start
```

### ITSI Episodes (premium) — incident rollup → Webex

The premium "episode" idea: instead of one alert per broken KPI, group the related alerts
into **one incident** and notify once. Two ways in this lab:

- **Config-as-code (works now):** the alert **"dcloud - Incident: services degraded →
  Webex episode"** (`alerts/default/savedsearches.conf`, disabled by default) rolls all
  degraded Berlin components into a single summary and posts **one** Webex message. Enable
  it in *Settings → Searches*, then run `demo-chaos.sh break-web` — one grouped ping, not five.
- **Native ITSI Notable Event Management (build in the UI):** create a **Correlation
  Search** on `index=itsi_summary kpi=ServiceHealthScore alert_level>=5`, then a **Notable
  Event Aggregation Policy** split by `service` with an action rule *"new episode → run
  Webex message"*. Watch it in **Episode Review**. (API-seeding NEM objects is
  version-fragile, so the UI is the supported path for the native version.)

### ITSI Glass Tables (premium) — three ready-to-import styles

Three native **GTv2 (Dashboard Studio) glass tables** ship as config-as-code, all reading
the **same host-keyed live signals** the ITSI KPIs use (`index=berlin_web
sourcetype=port:probe host=…` reachability, `index=*_metrics sourcetype=linux:metrics
host=…` heartbeats). Because they key on **host**, not on ITSI service `_key`s, they render
correctly and **survive every re-seed** — kill the DB and the red "blast radius" rolls up
the tree (Database → Directory App → Berlin → Global).

| Style | File | Best for |
|---|---|---|
| **NOC Ops Wall** | `itsi/glass_tables/glass_table_noc.json` | Big-screen SOC wall — glowing UP/DOWN tiles grouped by site under a global health hero |
| **Service Topology** | `itsi/glass_tables/glass_table_topology.json` | Dependency map with orthogonal connectors — shows blast radius |
| **Business Services** | `itsi/glass_tables/glass_table_exec.json` | Leadership view — branded cards with per-service KPI breakdown and a global-health ring |

```bash
# regenerate the files (source of truth is the generator, edit there not the JSON):
sudo -u splunk /opt/splunk/bin/splunk cmd python3 /opt/dcloud-splunk/itsi/seed_glass_table.py --write
```

**Import:** ITSI → *Dashboards / Glass Tables* → **Create Glass Table** → open the
**Source `</>`** editor → paste the contents of one JSON file → Save. Repeat for each.
(Or seed via the API — `seed_glass_table.py --seed --verbose` — best-effort and
reset-proof, but the GTv2 API schema shifts between versions, so import-by-hand always works.)

## Asset Configuration (inventory + enrichment)

The **Infrastructure Monitoring** app has an **Asset Configuration** view — describe a
host once (name, type, manufacturer, timezone, owner, description) and Splunk attaches that
context to **every event** from it. It's the OOTB version of Enterprise Security's Asset &
Identity framework (lookups).

- **Storage:** KV Store, **one collection per location** — `dcloud_assets_loc1`,
  `dcloud_assets_london`, `dcloud_assets_berlin` — seeded each session from
  `splunk/apps/infra_monitoring/lookups/assets_seed.csv` (`apply.sh` upserts it by `host`).
- **Enrichment:** automatic lookups on `host` add `asset_name`, `asset_type`,
  `manufacturer`, `timezone`, `description`, `owner` to metrics, syslog, Cisco and Proxmox
  events. Try: `index=* asset_type=router | stats count by manufacturer`.
- **Edit in the UI:** the Add/Edit form upserts a record with a plain
  `| … | outputlookup dcloud_assets_<site>_lk append=true` (core SPL, no add-on).

**RBAC — enforced per location (important):** KV Store lookups are *not* automatically
covered by index RBAC, so the collections are read/write-gated per role in
`metadata/default.meta`, matching the index wall:

| Inventory collection | Readable/editable by |
|---|---|
| `dcloud_assets_loc1` | `role_global` (admin) |
| `dcloud_assets_london` | `role_london`, `role_global` |
| `dcloud_assets_berlin` | `role_berlin`, `role_global` |

So **Ben sees/edits only Berlin**, Leo only London, Gary all — genuinely enforced at the
data layer (a raw `| inputlookup dcloud_assets_london_lk` returns nothing for Ben). The
lookup *definitions* stay globally readable so the automatic props enrichment loads for
every role without "Could not load lookup" warnings; the collection ACL is what gates the data.

## Client → Hypervisor → App correlation

A full end-to-end scenario in the **Correlation** app (*Client → Hypervisor → App*): a user
on the **Linux desktop (London)** reaches a **web-app** running in an **LXC container** on
the **Proxmox hypervisor (Berlin)** — and Splunk lines up all three layers with a live
status per node, correlated by time (and `src_ip` for traffic).

| Layer | Data | Index / sourcetype |
|---|---|---|
| Client (Linux desktop) | live metrics, logged-on users, top processes | `london_metrics` (`linux:metrics`), `london_linux` (`linux:sessions`, `linux:ps`) |
| Hypervisor (Proxmox) | node metrics, active containers, reachability | `berlin_proxmox` (`proxmox:api`), `berlin_metrics` (`proxmox:metrics`) |
| App (web-app container) | HTTP access log + host metrics, reachability | `berlin_web` (`webapp:access`, `webapp:probe`), `berlin_metrics` |

Set-up is Startup steps 4 + 5 (install the desktop UF, create the web-app + DB
containers, generate traffic). Then open **Correlation → Client → Hypervisor → App** as
**gary** — the correlation spans London + Berlin, so only **`role_global`** sees the whole
chain (Leo sees only the client side, Ben only Berlin; cross-domain correlation is a
global-analyst capability).

- **The web tier (Directory App):** LXC `webapp-berlin` (VMID 200) at `198.18.3.50:8080` —
  a stdlib Python app (`webapp/app.py`) that renders an **org chart** (employees grouped by
  team, read from PostgreSQL) with an **add-employee** form; every request is logged and an
  in-container UF ships the access log + host metrics. In ITSI, the web + DB tiers roll up
  into one **Directory App** service under Berlin.
- **Reachability probe:** the ubuntu-berlin UF hits `…:8080/healthz` every 60s →
  `berlin_web` (`webapp:probe`, `reachable`/`latency_ms`).
- The "locations" are subnets in one physical site (no NAT, full routing), so the client
  IP appears verbatim in the web-app access log and `src_ip` correlation is exact.

### Live failure-injection demo (`ubuntu/demo-chaos.sh`)

The best live moment: **break something and watch Splunk + ITSI catch it.** Run on the
Proxmox host (as root) — or on ubuntu-berlin with sudo:

```bash
# stop the web-app (or: break-db / break-all), then recover
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/demo-chaos.sh | bash -s -- break-web
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/demo-chaos.sh | bash -s -- recover
```

Within a few minutes the ITSI **Web / Database Service** (and the **Global IT Operations**
rollup) go **red** via the *App/DB Reachability* KPI; **Correlation → Root Cause Analysis**
pinpoints the down layer; and if you enabled the Webex alert, a Webex message fires.
`recover` turns it all green again.

## Splunk MCP Server (Claude Desktop)

Splunk ships a first-party **MCP Server** app ([Splunkbase app 7931](https://splunkbase.splunk.com/app/7931))
that exposes an MCP endpoint on the management port (`https://<host>:8089/services/mcp`),
so an MCP client like **Claude Desktop** can search Splunk in plain language.

**Install is opt-in and non-interactive.** Pass your splunk.com creds as **env vars** when
running `apply.sh` (nothing is committed to the repo):

```bash
sudo SPLUNKBASE_USERNAME='you@example.com' SPLUNKBASE_PASSWORD='...' bash /opt/dcloud-splunk/apply.sh
# install more Splunkbase apps too:  SPLUNKBASE_APP_IDS="7931 <id> ..."
```

`lib/splunkbase_install.py` logs in → finds the latest release → downloads → extracts into
`etc/apps/`; the restart in `apply.sh` loads it.

> **HTTP 403 on the download?** Splunkbase requires you to **accept the app's license terms
> once in a browser** first. Log in at
> [splunkbase.splunk.com/app/7931](https://splunkbase.splunk.com/app/7931), click
> **Download**, accept the terms (you can cancel the actual download), then re-run
> `apply.sh`. (No splunk.com entitlement? Host the `.spl` at a URL and use
> `SPLUNK_INSTALL_URLS` instead — see [Install ITSI / ITE-W](#install-itsi--it-essentials-work).)

**Then, per session** (runtime state, so recreate each time): grant the app's MCP
capability to your user/role (or use `admin`), create a **bearer token** (*Settings →
Tokens*), and point Claude Desktop at Splunk. The **Lab Overview → MCP Server & Claude
Desktop** dashboard walks through it and shows whether the app loaded. The
`claude_desktop_config.json` snippet:

```json
{
  "mcpServers": {
    "splunk": {
      "command": "npx",
      "args": ["-y", "mcp-remote",
        "https://198.18.1.124:8089/services/mcp",
        "--header", "Authorization: Bearer <YOUR_TOKEN>"]
    }
  }
}
```

> Caveats: app 7931 is certified for Splunk **8.0–10.2** (this box is **10.4.0** — the
> dashboard's status panel confirms it loaded); for the self-signed cert you may need
> `[mcp] ssl_verify = false` in the app's `mcp.conf`, and Claude Desktop must reach
> `198.18.1.124:8089`.

## Save to GitHub (persisting demo changes)

The lab wipes each session, so anything a customer builds live (e.g. a new dashboard) is
lost unless it's pushed back to the repo. The **Save to GitHub** dashboard does exactly
that: type a **branch name** and click Submit; it commits the running lab apps (including
`local/` changes) to a **new branch off `main`** for you to review and merge.

**How it works.** A dashboard button runs in Splunk's search sandbox, which has **no
outbound network**, so it can't `git push` directly. Instead the dashboard **enqueues** the
request to a KV Store (`git_save_requests`) — a local write — and a **scripted-input
watcher** (`bin/git_push_watcher.py`, runs every 30s in splunkd context, where network
works) drains the queue and runs `bin/labsync.sh <branch> <message>` to create and push the
branch. Status shows on the dashboard within ~30s. Branch names are slugified; a name
collision appends a timestamp (nothing is overwritten).

**Setup — provide a GitHub token.** Pushing needs write access, so create a **fine-grained
PAT** with **Contents: Read and write** on `js-csco/splunk-dcloud`. `apply.sh` **prompts
for it during setup** (never echoed) and stores it (mode 600, owned by the splunk user) at
`$SPLUNK_HOME/var/lib/dcloud/gh.token` — **outside** the app dir, so it's never committed.
Blank at the prompt keeps any existing token. For **unattended** runs:

```bash
# A) env var before apply.sh (skips the prompt):
sudo GITHUB_TOKEN=github_pat_xxx bash /opt/dcloud-splunk/apply.sh
# B) or drop it onto a running box by hand:
sudo mkdir -p /opt/splunk/var/lib/dcloud
printf '%s' 'github_pat_xxx' | sudo tee /opt/splunk/var/lib/dcloud/gh.token >/dev/null
sudo chmod 600 /opt/splunk/var/lib/dcloud/gh.token && sudo chown splunk:splunk /opt/splunk/var/lib/dcloud/gh.token
```

**Test the push logic standalone** (takes a branch name, no dashboard needed):

```bash
sudo -u splunk env SPLUNK_HOME=/opt/splunk bash \
  /opt/splunk/etc/apps/dcloud_lab/bin/labsync.sh added-dashboard "added a dashboard"
```

> Security: the token grants write access and lives on the box for the session; anyone who
> can use the dashboard triggers a push. The dashboard and the request queue are restricted
> to `admin`, and it only ever pushes to a **new branch**, never directly to `main`.

---

# Reference

## Get Data In (ingestion methods)

The **Get Data In** app demonstrates the ways to bring data into Splunk. Pull-based methods
use **scripted inputs** (a script Splunk runs on a schedule, in splunkd's context, so
outbound calls work — unlike the search sandbox).

| Method | How | Status |
|---|---|---|
| Syslog | rsyslog → per-location ports | ✅ live |
| Metrics (host) | UF `collect_host_metrics.sh` → `*_metrics` (key=value events, timechart) every 60s | ✅ live (loc1 always; london/berlin after `install-uf.sh`) |
| File monitor | UF tails `/var/log` → `*_linux` | ✅ live after `install-uf.sh` |
| REST / API (Proxmox) | UF on ubuntu-berlin polls the local Proxmox API every 60s → `berlin_proxmox` (events) + `berlin_metrics` (metrics) | ✅ live after `install-uf.sh berlin` |
| SSH (Cisco Catalyst) | scripted input SSHes in, runs show commands → `london_network`/`berlin_network` | ✅ live |
| SNMP | Splunk Connect for SNMP (SC4SNMP), dedicated VM → HEC → `berlin_snmp` | ✅ live after `snmp/setup-sc4snmp.sh` |
| SOAP | XML web service | ⏸ parked |

The app also has an **Indexes and Sourcetypes** dashboard (a live `| tstats` table of every
index, its sourcetypes and event counts, RBAC-aware, click-a-row-to-search) and a **Data
Model & Pivot** dashboard (a `DCloudLab` data model — *Lab Events*, *Host Metrics*, *Web
Requests*, *Network*, plus asset fields — so newcomers can build tables/charts in **Pivot**
with no SPL; model in `dcloud_lab/default/data/models/DCloudLab.json`, acceleration off).

**Syslog receivers.** The **Infrastructure Monitoring** app enables one syslog (TCP) port
per location on the indexer so RBAC is preserved — data can only land in its own location's
index (London → 5514 → `london_linux`; Berlin → 5515 → `berlin_linux`; loc1 → 5513 →
`loc1_linux`). `9997` is also enabled for Universal Forwarders. Run
`forward-to-splunk.sh <site>` (Startup steps 2–3) on each Ubuntu box; it configures rsyslog
(built in — no downloads, forwards to the indexer IP so no DNS needed).

**Proxmox REST poller.** Works out of the box using the committed lab creds in
`splunk/apps/get_data_in/bin/proxmox_config.env` (Berlin Proxmox `198.18.3.11`,
`root@pam` / `C1sco12345`, **ticket auth** — no API token needed). Override without editing
the repo via env or `$SPLUNK_HOME/var/lib/dcloud/proxmox.env` (precedence: committed config
→ `var/lib/dcloud/proxmox.env` → env vars):

```bash
export PROXMOX_HOST=198.18.3.11 PROXMOX_USER='root@pam' PROXMOX_PASSWORD='cisco'   # ticket auth
# … or an API token:  export PROXMOX_TOKEN='user@pam!lab=xxxxxxxx-....'
```

**Distributed collection (recommended architecture).** Instead of the indexer reaching
across to Proxmox, a **UF on ubuntu-berlin** pulls the *local* Proxmox API and forwards to
the indexer — "one agent collects everything (files + the local API)". The central poll is
disabled by default (`get_data_in` proxmox input `disabled=1`); `install-uf.sh berlin`
deploys `TA-dcloud-host` plus `TA-dcloud-proxmox` (runs via the host's `python3` since the
UF has no bundled Python). To go back to central polling, set the input `disabled=0` and
skip the UF.

```bash
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/install-uf.sh | sudo bash -s -- berlin
# if the pinned UF version 404s, pass the current URL:
#   ... | sudo SPLUNK_UF_URL='https://download.splunk.com/.../splunkforwarder-XX-Linux-x86_64.tgz' bash -s -- berlin
```

**Change guest state live (API write demo).** `ubuntu/proxmox-guest.py` starts/stops VMs &
containers via the API (ticket auth, nothing to pre-create). Within ~60s the **Running
guests over time** chart on the *REST API — Proxmox* dashboard reflects it; each action also
logs a `proxmox-ctl` syslog event:

```bash
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/proxmox-guest.py | python3 - list
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/proxmox-guest.py | python3 - stop 101
```

**Lab systems & access (throwaway demo creds, also shown on the Lab Info dashboard):**

| System | Location | Address | Access | User / Pass |
|---|---|---|---|---|
| Proxmox | Berlin | 198.18.3.11 (web 8006 / SSH) | Web + SSH | `root` / `C1sco12345` |
| Cisco Cat8kv | London | 198.18.2.32 | SSH | `cisco` / `cisco` |
| Cisco Cat8kv | Berlin | 198.18.3.32 | SSH | `cisco` / `cisco` |

## SNMP via Splunk Connect for SNMP (SC4SNMP)

Real-time SNMP into Splunk the **supported** way. SC4SNMP is a small Docker-Compose stack
(Mongo, Redis, workers, scheduler, a trap receiver and a sender) that **polls** devices
(GET, UDP 161) and **receives traps** (UDP 162), then ships to Splunk over **HEC**. It runs
on a **dedicated VM** (`ubuntu-berlin-snmp`, 198.18.3.52) so it's independent of Proxmox.
SNMP is **RBAC-scoped**: Berlin devices land in `berlin_snmp` (London in `london_snmp` once
routers are added). `apply.sh` enables **HEC** on the Splunk box (port 8088, fixed lab token
→ `berlin_snmp`). Then:

```bash
# 1) enable each device's SNMP agent (community 'dcloud') — Linux hosts:
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/snmp/enable-snmpd.sh | sudo bash
#    (snmp/enable-proxmox-snmpd.sh remains as the Proxmox-only shorthand)
# 2) routers — apply the SNMP lines already in routers/{berlin,london}-cat8kv.txt
#    (snmp-server community dcloud RO + trap host 198.18.3.52). Paste over console/SSH.
# 3) on ubuntu-berlin-snmp (198.18.3.52) — stand up the SC4SNMP Docker stack.
#    Polls 198.18.3.11 (Proxmox), 198.18.3.32 + 198.18.2.32 (routers), 198.18.2.11 (desktop):
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/snmp/setup-sc4snmp.sh | sudo -E bash
#    (override the pinned tag with SC4SNMP_REF=vX.Y.Z ; HEC/index default to the lab values)
```

Watch it in **Get Data In → SNMP — Splunk Connect (SC4SNMP)** (index `berlin_snmp`).

> The polled device list is `inventory.csv` in the SC4SNMP `docker_compose` dir (seeded
> from `setup-sc4snmp.sh`; add/remove rows and it re-reads). SC4SNMP's compose layout /
> `.env` keys change between versions, so `setup-sc4snmp.sh` clones the **official** repo and
> only overrides our values (HEC host/token/index, inventory) — on first run, confirm against
> the cloned `docker_compose/.env` of the `SC4SNMP_REF` you pinned.

## Install ITSI / IT Essentials Work

ITSI and **IT Essentials Work (ITE-W, Splunkbase app 5403)** are the **same package** — one
download. With **no ITSI license** it runs as the free **ITE-W** (entities, services, KPIs,
Service Analyzer); add a valid **ITSI license** and the premium features (glass tables,
ML/adaptive thresholding, episode/notable management) unlock — same app, no reinstall.
**Use the 5.0.x line** (5.0.1 is current; supports Splunk **10.2–10.5**, matching this box's
**10.4.0**). The older 4.20 line targets Splunk 9.x — don't use it here.

Per Splunk's docs it installs **only by extracting the `.spl` into `etc/apps`** — **not**
Splunk Web upload, **not** `splunk install app`. The easiest lab path is to host the `.spl`
at a URL and let `apply.sh` fetch + extract it server-side (survives every reset). This
needs no Splunkbase entitlement or terms acceptance:

```bash
sudo SPLUNK_INSTALL_URLS="https://your-host/it-essentials-work_501.spl" \
     ITSI_LICENSE_URL="https://your-host/itsi.lic" \
     bash /opt/dcloud-splunk/apply.sh
```

`ITSI_LICENSE_URL` is **optional** — omit it to run as free ITE-W; add it (or load the
license later via *Settings → Licensing*) to unlock full ITSI. `apply.sh` auto-installs
**OpenJDK 17** when it sees the `itsi` app (Ubuntu 24.04's default Java 21 is unsupported)
and stages the license into `etc/licenses/enterprise` before starting Splunk.
`SPLUNK_INSTALL_URLS` works for any app from a direct URL; `SPLUNKBASE_APP_IDS="5403"` works
instead if your splunk.com account is entitled.

> Prefer not to host a URL? Extract manually on the Splunk box each session (`/tmp` is wiped
> on reset): stop Splunk, `tar -xf it-essentials-work_501.spl -C /opt/splunk/etc/apps`,
> start Splunk, then re-run `apply.sh` for the Java prerequisite. The **Lab Overview → ITSI
> Setup** dashboard has the full checklist.

**ITE-W installs empty.** Once the app is up, populate demo Entities, Services and KPIs —
built from data already flowing in the lab — with the one-time seeder:

```bash
sudo -u splunk /opt/splunk/bin/splunk cmd python3 \
  /opt/dcloud-splunk/itsi/seed_itsi_demo.py --user admin --password C1sco12345 --verbose
```

> Use `splunk cmd python3`, not `/opt/splunk/bin/python3` directly (the latter picks up the
> system OpenSSL and fails to import `ssl`). Best-effort: ITSI's REST schema shifts between
> versions, so `--verbose` prints any rejection to tune `itsi/seed_itsi_demo.py`. KPIs take
> a few scheduled runs to show values.

## Repo layout

```
bootstrap.sh                  # optional entry point (clone/refresh + hand off)
apply.sh                      # idempotent orchestrator (all the real logic)
config/lab.env                # tunables (paths, repo/branch, app name, creds source)
config/lab_users.csv          # lab users -> roles (demo passwords)
lib/common.sh                 # shared shell helpers (DNS fix, systemd, REST, roles, users)
lib/splunkbase_install.py     # Splunkbase login + download + extract
routers/                      # versioned Cisco Cat8kv baseline configs
snmp/                         # enable-snmpd + SC4SNMP setup
ubuntu/                       # forwarders, containers, chaos + activity generators
webapp/                       # stdlib Python org-chart app (Directory App web tier)
itsi/                         # ITSI/ITE-W + glass-table seeders and JSON
splunk/uf-apps/               # TA-dcloud-host, TA-dcloud-proxmox (Universal Forwarder TAs)
splunk/apps/
  dcloud_lab/                 # "Lab Overview": indexes, dashboards, datamodel, Save-to-GitHub
  get_data_in/                # ingestion-method demos + Proxmox/SSH pollers
  infra_monitoring/           # host metrics, asset config, syslog receivers
  alerts/                     # scheduled alerts + Webex/Proxmox actions
  correlation/                # 2/3-source + Client -> Hypervisor -> App dashboards
  itsi_episodes/              # config-as-code episode saved searches
```

> Roles are created via REST at runtime (in `apply.sh`), not from an `authorize.conf` file.

## Environment assumptions

- Cisco dCloud VM, Ubuntu 24.04, Splunk **10.4.0**, systemd-managed (`Splunkd.service`),
  already installed and running.
- Splunk UI at `http://198.18.1.124:8000`, admin login already provisioned.
- The startup command runs as root (or with sudo).

## Roadmap

- [x] Per-location indexes + RBAC (Leo/London, Ben/Berlin, Gary/global; loc1 = infra, global-only)
- [x] Dashboards: Lab Info, Setup Status, Ingestion & Health
- [x] Data onboarding: rsyslog from Ubuntu boxes + Splunk host self-forward (loc1)
- [x] Get Data In app: Proxmox REST, Cisco SSH, "Indexes and Sourcetypes" explorer, methods overview
- [x] Host & infra metrics: UF `collect_host_metrics.sh` + Proxmox metrics → `*_metrics` + dashboard
- [x] UF distributed collection on ubuntu-london & ubuntu-berlin (`install-uf.sh <site>`)
- [x] Alerts: CPU/mem >70% + "forwarder stopped sending" (email + Triggered Alerts + dashboard)
- [x] Asset Configuration: per-location KV Store inventory (RBAC-enforced) + automatic enrichment
- [x] Client → Hypervisor → App correlation: web-app + DB LXCs, in-container UF, reachability probe, correlation dashboard
- [x] Save to GitHub: branch-per-save via KV queue + splunkd-context watcher
- [x] Data model (DCloudLab) + Pivot: point-and-click analytics, RBAC-aware
- [x] Splunk MCP Server: opt-in Splunkbase install at boot + "MCP Server & Claude Desktop" how-to dashboard
- [x] SNMP via SC4SNMP (dedicated VM → HEC → `berlin_snmp`)
- [x] ITSI / ITE-W: install path (URL or Splunkbase), demo seeders, episodes + glass tables
- [ ] Real ITSI (premium): heavy on a reset-each-session VM — free ITE-W preferred unless licensed
- [ ] SOAP sender (parked)
```
