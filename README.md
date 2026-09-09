# splunk-dcloud

GitHub-driven, self-rebuilding **Splunk RBAC lab** for Cisco **dCloud**. The
dCloud VM resets to an empty template each session, so on every startup one
command pulls this repo and rebuilds the entire Splunk configuration from
scratch — per-location indexes, RBAC roles, users, and dashboards. Nothing is
configured by hand; the lab is identical every time.

To change the lab, edit files here and `git push` to `main` — the next session
picks it up automatically. The dCloud startup command never changes.

<img src="splunk/apps/dcloud_lab/appserver/static/topology.svg" alt="Lab topology" width="640">

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
        └── [later] data inputs (HEC / forwarder inputs for Ubuntu + Proxmox)
```

`bootstrap.sh` is an alternative entry point that clones/refreshes an existing
checkout and hands off to `apply.sh`; the canonical startup command below runs
`apply.sh` directly.

## Startup (each session)

Everything resets each session. Run these in order.

**1. Splunk box** — build the whole Splunk config (indexes, roles, users,
dashboards, SSH router polling):

```bash
sudo rm -rf /opt/dcloud-splunk && sudo git clone -b main https://github.com/js-csco/splunk-dcloud.git /opt/dcloud-splunk && sudo bash /opt/dcloud-splunk/apply.sh
```

**2. ubuntu-london** — forward logs (syslog → `london_linux`), then install the
Universal Forwarder (host metrics → `london_metrics`, `/var/log` → `london_linux`):

```bash
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/forward-to-splunk.sh | sudo bash -s -- london
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/install-uf.sh        | sudo bash -s -- london
```

**3. ubuntu-berlin** — forward logs, then install the Universal Forwarder (host
metrics → `berlin_metrics`, `/var/log` → `berlin_linux`, **and** the local
Proxmox API → `berlin_proxmox` + `berlin_metrics`):

```bash
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/forward-to-splunk.sh | sudo bash -s -- berlin
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/install-uf.sh        | sudo bash -s -- berlin
```

> The Splunk box collects **its own** host metrics automatically (`loc1_metrics`),
> so the **Host & Infra Metrics** app has data even before the UFs are installed.

**4. (optional) demo data** — correlated events on the Ubuntu boxes:

```bash
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/generate-activity.sh | bash
```

Then open `http://198.18.1.124:8000` → **Lab Overview → Setup Status** (all
green) and log in as `leo` / `ben` / `gary` (password `C1sco12345`).

> The Cisco routers (London/Berlin) are polled automatically by the Splunk box —
> nothing to run there.

### Troubleshooting: DNS

If a box can't resolve `github.com` (`ping 1.1.1.1` works but `ping google.com`
fails), fix the resolver, then re-run:

```bash
sudo rm -f /etc/resolv.conf
printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\noptions timeout:2 attempts:2\n' | sudo tee /etc/resolv.conf
```

> Always clone with `-b main` (a plain `git clone` may pull an older default branch).

## Location-based RBAC (the core design)

Access control is enforced at the **index** level. The golden rule: an index
belongs to exactly ONE location, so a role granting one location's indexes can
never leak another's. Indexes are named `loc<N>_<datatype>` so a role grants a
whole location with a single wildcard.

| Location | Network | Devices | Indexes | Roles with access |
|---|---|---|---|---|
| loc1 | 198.18.1.0/24 | splunk (infrastructure) | `loc1_linux`, `loc1_metrics` | `role_global` only |
| London (loc2) | 198.18.2.0/24 | ubuntu-london, windows-server-2022-london | `london_linux`, `london_windows`, `london_network`, `london_metrics` | `role_london`, `role_global` |
| Berlin (loc3) | 198.18.3.0/24 | proxmox-9.2-berlin, ubuntu-berlin | `berlin_linux`, `berlin_proxmox`, `berlin_network`, `berlin_metrics` | `role_berlin`, `role_global` |

> The `*_metrics` indexes need no RBAC changes — roles grant a whole location
> by wildcard (`london_*`, `berlin_*`, `loc1_*`), so they're covered
> automatically and the location wall still holds for metrics.

> Location 1 is the Splunk server itself (infrastructure), so it has **no
> dedicated analyst** — `loc1_linux` is visible to `role_global` only.

**Roles** (created at runtime via REST in `apply.sh`): `role_london → london_*`,
`role_berlin → berlin_*`, `role_global → london_*;berlin_*;loc1_*` (+ `_*` for
internal). REST is used instead of `authorize.conf` because on this
image app-level `authorize.conf` roles did not register even after a restart,
whereas REST creation is immediate and reliable.

> **Roles do NOT import the built-in `user` role.** On this image `user` grants
> `srchIndexesAllowed = *`, and Splunk *unions* inherited index access — so
> importing `user` would let a location role see every index. Instead each role
> is given explicit capabilities (search, rtsearch, …) and only its own
> indexes, which keeps the location wall airtight.

**Users** (`config/lab_users.csv`, created at boot):

| User | Role | Sees |
|---|---|---|
| `gary` | `role_global` | everything (London + Berlin + loc1 infra) |
| `leo` | `role_london` | London only (`london_*`) |
| `ben` | `role_berlin` | Berlin only (`berlin_*`) |

> Demo passwords live in `config/lab_users.csv` and are intended for a
> throwaway lab. Do not put real passwords there.

## Dashboards (in the "Lab Overview" app)

| Dashboard | Purpose |
|---|---|
| **Lab Info** (landing page) | What the lab is, the topology diagram, repo link, Splunk version, and the deploy command. |
| **Setup Status** | Post-deploy verification — green/red checklist confirming indexes, roles, users, and the app all loaded before the demo starts. |
| **Ingestion & Health** | Event volume per location index and Splunk health. |
| **Save to GitHub** (admin-only) | Type a branch name and Submit to commit the current lab state (including dashboards made this session) to a new branch for review/merge. See below. |

Plus these apps:

- **Host & Infra Metrics** → *Host Metrics* (CPU/mem/disk/load per machine — see "Host & infrastructure metrics" below).
- **Alerts** → *Alerts — status & demo* (CPU/mem threshold + forwarder-health alerts — see "Alerts" below).
- **Infrastructure Monitoring** → *Data Onboarding Overview* (what data is arriving, by host/index/sourcetype).
- **Correlation** → *Correlation 2 Sources* and *Correlation 3 Sources*: pick the
  sources and a correlation key (service / user / host) and find the same entity
  across separate sources in a time window (the canonical
  `stats count(eval(source=A)) … by key` technique, with the SPL shown on-screen).
  Each page has a plain-language explainer for Splunk newcomers.

**Generate correlated demo data:** run this on ubuntu-london, ubuntu-berlin (and
the Splunk host) so the same services/users appear across sources:
```bash
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/generate-activity.sh | bash
```
It emits syslog events (via `logger`) sharing services (authsvc, paymentsvc, …)
and users (alice, bob, …); correlate by **service** or **user** afterwards.

## Save to GitHub (persisting demo changes)

The lab wipes each session, so anything a customer builds live (e.g. a new
dashboard) is lost unless it's pushed back to the repo. The **Save to GitHub**
dashboard does exactly that: you type a **branch name** (e.g. `added-dashboard`)
and click Submit; it commits the running lab apps (including `local/` changes) to
a **new branch off `main`**. You then review and merge that branch into `main`,
and the next session pulls `main` and includes it.

**How it works (why it now works).** A dashboard button runs in Splunk's search
sandbox, which has **no outbound network**, so it can't `git push` directly (that
was the old error). Instead the dashboard **enqueues** the request to a KV Store
(`git_save_requests`) — a local write — and a **scripted-input watcher**
(`bin/git_push_watcher.py`, runs every 30s in splunkd context, where network
works) drains the queue and runs `bin/labsync.sh <branch> <message>` to create
and push the branch. Status shows on the dashboard within ~30s. Branch names are
slugified; if the name already exists a timestamp is appended (nothing is
overwritten).

**Setup — provide a GitHub token.** Pushing needs write access, so create a
**fine-grained PAT** with **Contents: Read and write** on `js-csco/splunk-dcloud`.
`apply.sh` **prompts you for it during setup** (reads from the terminal, never
echoed) and stores it (mode 600, owned by the splunk user) at
`$SPLUNK_HOME/var/lib/dcloud/gh.token` — **outside** the app dir, so it is never
captured or committed. Blank at the prompt keeps any existing token.

```text
GitHub token for "Save to GitHub" (fine-grained PAT, Contents: Read+Write; blank to skip): ****
```

For **unattended** runs (automation, no terminal), supply it another way instead:

```bash
# A) env var before apply.sh (skips the prompt):
sudo GITHUB_TOKEN=github_pat_xxx bash /opt/dcloud-splunk/apply.sh

# B) or drop it onto a running box by hand:
sudo mkdir -p /opt/splunk/var/lib/dcloud
printf '%s' 'github_pat_xxx' | sudo tee /opt/splunk/var/lib/dcloud/gh.token >/dev/null
sudo chmod 600 /opt/splunk/var/lib/dcloud/gh.token
sudo chown splunk:splunk /opt/splunk/var/lib/dcloud/gh.token
```

**Test the push logic standalone** (no dashboard needed — takes a branch name):

```bash
sudo -u splunk env SPLUNK_HOME=/opt/splunk bash \
  /opt/splunk/etc/apps/dcloud_lab/bin/labsync.sh added-dashboard "added a dashboard"
# -> {"status":"ok","message":"Saved N file(s) to branch added-dashboard ...","commit":"...","branch":"added-dashboard"}
```

> Security: the token grants write access to the repo and lives on the box for
> the session; anyone who can use the dashboard triggers a push. The dashboard
> and the request queue are restricted to `admin`. It only ever pushes to a **new
> branch**, never directly to `main`.

## Sending data in (Ubuntu → Splunk)

The **Infrastructure Monitoring** app enables receivers on the indexer, one
syslog (TCP) port per location so RBAC is preserved — data can only land in its
own location's index:

| Sender | → Port | → Index | Seen by |
|---|---|---|---|
| ubuntu-london (London devices) | 5514 | `london_linux` | role_london, role_global |
| ubuntu-berlin, proxmox-9.2-berlin | 5515 | `berlin_linux` | role_berlin, role_global |
| Splunk host itself (loc1) | 5513 | `loc1_linux` | role_global |

Port `9997` is also enabled for a Universal Forwarder as a future upgrade.

Run the matching line on each Ubuntu box (location passed explicitly for a
clean setup flow):

**On `ubuntu-london`:**
```bash
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/forward-to-splunk.sh | sudo bash -s -- london
```

**On `ubuntu-berlin`:**
```bash
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/forward-to-splunk.sh | sudo bash -s -- berlin
```

(Or omit the argument to auto-detect the location from the hostname/IP:
`curl -fsSL <url> | sudo bash`.)

It configures rsyslog (built into Ubuntu — no downloads, forwards to the
indexer IP so no DNS needed) to ship all logs to the right port, and emits a
marker event. Verify in Splunk: `index=london_linux host=ubuntu-london`, or open
**Infrastructure Monitoring → Data Onboarding Overview**.

> If `raw.githubusercontent.com` doesn't resolve on the Ubuntu box, clone the
> repo (like the Splunk box) and run `ubuntu/forward-to-splunk.sh` from it.

## Host &amp; infrastructure metrics

The **Host & Infra Metrics** app charts CPU / memory / disk / load for every
machine.

**How the numbers get in:** a small agent samples the OS every 60s and emits one
`key=value` line per sample. These land in per-location **event** indexes
(`*_metrics`) and are charted with `timechart`; Splunk's automatic `key=value`
extraction turns `cpu_pct=…`, `mem_used_pct=…`, etc. into fields with no extra
config.

| Source | Collector | → Index | Notes |
|---|---|---|---|
| Splunk host (loc1) | local scripted input (`metrics` app) | `loc1_metrics` | always on — no forwarder/network needed |
| ubuntu-london | UF `TA-dcloud-host` (`collect_host_metrics.sh`) | `london_metrics` | after `install-uf.sh london` |
| ubuntu-berlin | UF `TA-dcloud-host` | `berlin_metrics` | after `install-uf.sh berlin` |
| Proxmox (Berlin) | UF `TA-dcloud-proxmox` (`poll_proxmox.py metrics`) | `berlin_metrics` | per-node & per-guest CPU/mem/disk |

Query/verify (these are normal event indexes, so the raw samples are directly
searchable):

```spl
index=loc1_metrics OR index=london_metrics OR index=berlin_metrics sourcetype=linux:metrics | timechart span=1m avg(cpu_pct) by host
```

> **Why event indexes, not metric indexes?** Metric indexes + ingest-time
> log-to-metrics are elegant but fragile from a Universal Forwarder (a UF that
> does structured extraction forwards pre-cooked events that bypass the
> indexer's metric-schema transform, so the metric index silently drops them).
> Event indexes + `timechart` are reliable across the self-rebuilding lab and,
> crucially, **directly searchable** — `index=london_metrics` shows the raw
> samples, so data arrival is trivial to confirm.

**How we get metrics from Proxmox.** The Proxmox REST API already returns
per-node and per-guest CPU / memory / disk (from `/cluster/resources`) — we
already poll it. The same UF poller (`poll_proxmox.py metrics`, run by
`poll_proxmox_metrics.sh` every 60s) emits those numbers as metric JSON into
`berlin_metrics` (sourcetype `proxmox:metrics`), so they appear on both the
**Host & Infra Metrics** app and the *REST API — Proxmox* dashboard. No Proxmox
metric-server (InfluxDB/Graphite) or extra agent is needed — it's the same
ticket-auth API call, just emitted as metrics. (Requires the Berlin network to
reach `198.18.3.17:8006`.)

> **RBAC preserved:** metrics land in per-location indexes, so Leo sees London,
> Ben sees Berlin, Gary sees all — same wall as the logs.

## Alerts

The **Alerts** app ships two scheduled alerts (config-as-code in
`splunk/apps/alerts/default/savedsearches.conf`, rebuilt every session). Both run
every 5 minutes, trigger on `>0` results, email **js-csco@proton.me**, and are
tracked so they show under **Activity → Triggered Alerts** and on the *Alerts*
dashboard.

| Alert | Fires when | Search basis |
|---|---|---|
| **High CPU or Memory (>70%)** | any host's latest CPU% or mem% > 70% | `*_metrics` (`linux:metrics`) |
| **Universal Forwarder stopped sending** | a host sent metrics before but nothing for >10 min | `tstats latest(_time) by host` over `*_metrics` |

**Email delivery (important):** the alert *logic* fires with no setup, but
sending mail needs an SMTP relay Splunk can reach. The dCloud lab has none by
default and Proton Mail doesn't accept arbitrary SMTP, so **email won't leave the
lab until you configure a relay** — either in `alerts/default/alert_actions.conf`
(`[email] mailserver = host:port`) or via **Settings → Server settings → Email
settings**. The alert still fires and is visible in Splunk regardless.

**Trigger them for a demo** (the *Alerts* dashboard panels refresh every 30s, so
you can watch a row go red before the scheduled run; or open the saved search and
click **Run** to fire immediately):

```bash
# CPU > 70% on an Ubuntu box (or the Splunk box, for site loc1) — each worker
# self-stops after 240s (orphan-safe). Stop early any time with: pkill -x yes
for i in $(seq $(nproc)); do timeout 240 yes >/dev/null & done; wait

# Universal Forwarder stopped sending — stop the UF, then restart to clear
sudo /opt/splunkforwarder/bin/splunk stop
sudo /opt/splunkforwarder/bin/splunk start
```

## Asset Configuration (asset inventory + enrichment)

The **Infrastructure Monitoring** app has an **Asset Configuration** view — describe
a host once (name, type, manufacturer, timezone, owner, description) and Splunk
attaches that context to **every event** from it. It's the OOTB version of what
Enterprise Security calls the Asset & Identity framework (lookups), and it maps to
the "Asset Configuration" feature customers know from other tools.

- **Storage:** KV Store, **one collection per location** — `dcloud_assets_loc1`,
  `dcloud_assets_london`, `dcloud_assets_berlin` — seeded each session from the
  committed `splunk/apps/infra_monitoring/lookups/assets_seed.csv` (config-as-code
  source of truth; `apply.sh` upserts it into the KV Store by `host`).
- **Enrichment:** automatic lookups on `host` add `asset_name`, `asset_type`,
  `manufacturer`, `timezone`, `description`, `owner` to metrics, syslog, Cisco and
  Proxmox events. Try: `index=* asset_type=router | stats count by manufacturer`.
- **Edit in the UI:** the Add/Edit form upserts a record with a plain
  `| … | outputlookup dcloud_assets_<site>_lk append=true` (core SPL, no add-on).
  Enter an existing **Host** to edit that row.

**RBAC — enforced per location (important):** KV Store lookups are *not*
automatically covered by index RBAC, so the inventory is scoped **deliberately**.
Each collection + lookup definition is read/write-gated per role in
`metadata/default.meta`, matching the index wall:

| Inventory | Readable/editable by |
|---|---|
| `dcloud_assets_loc1` | `role_global` (admin) |
| `dcloud_assets_london` | `role_london`, `role_global` |
| `dcloud_assets_berlin` | `role_berlin`, `role_global` |

So **Ben sees/edits only Berlin**, Leo only London, Gary all — and it's genuinely
enforced (even a raw `| inputlookup dcloud_assets_london_lk` is denied for Ben),
not just hidden in the dashboard. Event *enrichment* follows the same wall for
free, since users only ever see events from indexes they're allowed to read.

## Client → App → Hypervisor correlation

A full end-to-end scenario in the **Correlation** app (*Client → App → Hypervisor*):
a user logs into **Windows (London)**, opens a **web-app** running in an **LXC
container** on the **Proxmox hypervisor (Berlin)**, and clicks a button — and
Splunk lines up all four layers, correlated by `src_ip` and time.

**Components / data:**

| Layer | Data | Index / sourcetype |
|---|---|---|
| Windows client | logons (4624/4625), CPU/mem | `london_windows` (WinEventLog), `london_metrics` (perfmon) |
| Web-app container | HTTP access log (method/path/src_ip/action) + host metrics | `berlin_web` (`webapp:access`), `berlin_metrics` |
| Hypervisor | metrics, active containers, **is the app reachable?** | `berlin_proxmox`, `berlin_web` (`webapp:probe`) |

**Set it up (each session):**

```bash
# 1) On ubuntu-berlin — create the LXC on Proxmox + provision app + in-container UF
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/create-webapp-container.sh | sudo bash
#    (re-run ubuntu/install-uf.sh berlin too, to enable the reachability probe)

# 2) On the Windows client (elevated PowerShell) — Windows UF (events + perfmon)
Set-ExecutionPolicy Bypass -Scope Process -Force
iwr https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/windows/install-uf.ps1 -UseBasicParsing | iex

# 3) Log into Windows, browse to http://198.18.3.50:8080/, click "Do something"
```

Then open **Correlation → Client → App → Hypervisor** (as **gary** — see note below).

- **The web-app container:** LXC `webapp-berlin` (VMID 200) at `198.18.3.50:8080`,
  created via `pct` over SSH to Proxmox; a tiny stdlib Python app (`webapp/app.py`)
  logs every request; an in-container UF ships the access log + host metrics.
- **Reachability probe:** the ubuntu-berlin UF hits `…:8080/healthz` every 60s →
  `berlin_web` (`webapp:probe`, `reachable`/`latency_ms`).

**RBAC:** the correlation spans London + Berlin, so only **`role_global` (gary)**
sees the whole chain; Leo sees only the Windows side, Ben only the Berlin side —
cross-domain correlation is a global-analyst capability.

**Notes:**
- The four "locations" are just four subnets in one physical site — no NAT, full
  routing between them — so the Windows client IP appears verbatim in the web-app
  access log and `src_ip` correlation is exact.
- **Depends on Proxmox being deployed/reachable** (host `198.18.3.17`, SSH root):
  the container is created there via `pct`. The create script auto-detects
  storage/template/gateway; override with `CT_STORAGE`/`CT_TEMPLATE`/`CT_GW`/
  `CT_BRIDGE` if the lab uses non-default names.

## Splunk MCP Server (Claude Desktop) — manual, for now

Splunk ships a first-party **MCP Server** app ([Splunkbase app 7931](https://splunkbase.splunk.com/app/7931))
that exposes an MCP endpoint on the management port: `https://<host>:8089/services/mcp`.
It is **not** yet wired into `apply.sh` — the app is Splunkbase-only (authenticated
download + license acceptance), so it can't be fetched unattended. Until we pick a
sourcing method (vendor the `.tgz` in this repo, or download at boot with Splunkbase
creds), install it by hand each session:

1. **Install the app** on the Splunk box: Splunk Web → *Apps → Manage Apps →
   Install app from file*, upload the app 7931 `.tgz`, then restart Splunk.
2. **Grant the MCP capabilities** to a role (e.g. add to `role_global`):
   `mcp_tool_execute` (and `mcp_tool_admin` for full control).
3. **Create a bearer token**: Splunk Web → *Settings → Tokens* (enable token auth
   if prompted) → new token for the MCP user; copy it.
4. **Point Claude Desktop at it** via the `mcp-remote` proxy
   (`claude_desktop_config.json`):

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

> Caveat: app 7931 is certified for Splunk **8.0–10.2**; this box is **10.4.0**, so
> confirm it loads before relying on it. For a self-signed cert you may need to allow
> insecure TLS in the app's `mcp.conf` (`[mcp] ssl_verify = false`).
>
> Because the VM resets each session, this is manual until automated. When ready,
> the plan is: drop the `.tgz` in `splunk/vendor/`, and `apply.sh` extracts + enables
> it, grants the capability, and prints this snippet.

## Get Data In (ingestion methods)

The **Get Data In** app demonstrates the ways to bring data into Splunk. Pull-based
methods use **scripted inputs** (a script Splunk runs on a schedule — runs in
splunkd's context, so outbound calls work, unlike the search sandbox).

> The app also has an **Indexes and Sourcetypes** dashboard — a live table of every
> index, the sourcetypes in it, and event counts (via `| tstats`), respecting RBAC.
> Click any row to open that `index`/`sourcetype` in Search. A good first stop for
> "what data do I have, and how do I start a search?"

| Method | How | Status |
|---|---|---|
| Syslog | rsyslog → per-location ports | ✅ live |
| Metrics (host) | UF `collect_host_metrics.sh` → `*_metrics` (key=value events, charted with timechart) every 60s | ✅ live (loc1 always; london/berlin after `install-uf.sh`) |
| File monitor | UF tails `/var/log` → `*_linux` | ✅ live after `install-uf.sh` |
| REST / API (Proxmox) | UF on ubuntu-berlin polls the local Proxmox API every 60s → `berlin_proxmox` (events) + `berlin_metrics` (metrics) | ✅ live after `install-uf.sh berlin` |
| SSH (Cisco Catalyst) | scripted input SSHes in, runs show commands → `london_network`/`berlin_network` | ✅ live (London 198.18.2.32, Berlin 198.18.3.32) |
| SNMP | Splunk Connect for SNMP (SC4SNMP) | ⏳ planned |
| SOAP | XML web service | ⏸ parked |

**Proxmox REST poller** — works out of the box using the committed lab creds in
`splunk/apps/get_data_in/bin/proxmox_config.env` (Berlin Proxmox `198.18.3.17`,
`root` / `cisco`, **ticket auth** — no API token needed). To override without
editing the repo (e.g. real creds/token), set env at startup or drop
`$SPLUNK_HOME/var/lib/dcloud/proxmox.env`:

```bash
# either username/password (ticket auth) …
export PROXMOX_HOST=198.18.3.17 PROXMOX_USER='root@pam' PROXMOX_PASSWORD='cisco'
# … or an API token
export PROXMOX_TOKEN='user@pam!lab=xxxxxxxx-....'
```

Precedence: committed config → `var/lib/dcloud/proxmox.env` → env vars.

**Distributed collection (UF in-location) — the recommended architecture.** Instead
of the indexer reaching across to Proxmox, run a **Universal Forwarder on
ubuntu-berlin** that pulls the *local* Proxmox API and forwards to the indexer —
"one agent collects everything (files + the local API)". The central poll is
disabled by default (`get_data_in` proxmox input `disabled=1`); the UF does it.

On **ubuntu-berlin** (pass the site so the UF targets Berlin's indexes):
```bash
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/install-uf.sh | sudo bash -s -- berlin
# if the pinned UF version 404s, pass the current URL from splunk.com:
#   ... | sudo SPLUNK_UF_URL='https://download.splunk.com/.../splunkforwarder-XX-Linux-x86_64.tgz' bash -s -- berlin
```
It installs the UF, points `outputs.conf` at the indexer's `9997` receiver, and
deploys `TA-dcloud-host` (host metrics + `/var/log`) plus — on Berlin only —
`TA-dcloud-proxmox` (localized Proxmox poller, runs via the host's `python3`
since the UF has no bundled Python). To go back to central Proxmox polling, set
the `poll_proxmox.py` input `disabled=0` in `get_data_in` and skip the UF.

**Change guest state live (API write demo).** `ubuntu/proxmox-guest.py` starts/stops
VMs & containers via the API (ticket auth — no token; nothing to pre-create even
though Proxmox resets each session). Run from any box that can reach Proxmox:
```bash
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/proxmox-guest.py | python3 - list
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/proxmox-guest.py | python3 - stop 101
```
Within ~60s the **Running guests over time** chart on the *REST API — Proxmox*
dashboard reflects it. Each action also logs a `proxmox-ctl` syslog event.

### Lab systems &amp; access (demo creds)

| System | Location | Address | Access | User / Pass |
|---|---|---|---|---|
| Proxmox | Berlin | 198.18.3.17 (web 8006 / SSH) | Web + SSH | `root` / `cisco` |
| Cisco Cat8kv | London | 198.18.2.32 | SSH | `cisco` / `cisco` |
| Cisco Cat8kv | Berlin | 198.18.3.32 | SSH | `cisco` / `cisco` |

> These are throwaway lab creds (also shown on the **Lab Info** dashboard). Don't
> reuse real secrets in the repo.

**Architecture note:** the REST/SSH pollers run centrally on the Splunk host as
scripted inputs. In production you'd run them on a forwarder *in each location*;
data still lands in the correct per-location index either way, so RBAC is
unaffected — only the collection topology differs.

## Repo layout

```
bootstrap.sh                  # optional entry point (clone/refresh + hand off)
apply.sh                      # idempotent orchestrator (all the real logic)
config/lab.env                # tunables (paths, repo/branch, app name, creds source)
config/lab_users.csv          # lab users -> roles (demo passwords)
lib/common.sh                 # shared shell helpers (DNS fix, systemd, REST, roles, users)
splunk/apps/dcloud_lab/
  default/
    app.conf                  # app manifest (display name: "Lab Overview")
    indexes.conf              # per-location indexes
    data/ui/nav/default.xml   # app navigation
    collections.conf          # KV Store: Save-to-GitHub request queue
    transforms.conf           # KV Store lookup for the queue
    inputs.conf               # git-push watcher scripted input
    data/ui/views/lab_info.xml       # landing page
    data/ui/views/setup.xml          # setup verification
    data/ui/views/lab_overview.xml   # ingestion & health
    data/ui/views/save_to_github.xml # admin: enqueue a save -> push a named branch
  bin/labsync.sh              # git branch + snapshot + push (runnable standalone)
  bin/git_push_watcher.py     # scripted input: drains the queue -> labsync.sh
  appserver/static/topology.svg      # topology diagram (used by Lab Info + this README)
  metadata/default.meta       # sharing/permissions
```

> Roles are created via REST at runtime (in `apply.sh`), not from an
> `authorize.conf` file.

## Environment assumptions

- Cisco dCloud VM, Ubuntu 24.04, Splunk **10.4.0**, systemd-managed
  (`Splunkd.service`), already installed and running.
- Splunk UI at `http://198.18.1.124:8000`, admin login already provisioned.
- The startup command runs as root (or with sudo).

## Roadmap

- [x] Per-location indexes
- [x] RBAC: role_london (Leo), role_berlin (Ben), role_global (Gary); loc1 = infra, global-only
- [x] Dashboards: Lab Info, Setup Status, Ingestion & Health
- [x] Data onboarding: rsyslog from Ubuntu boxes + Splunk host self-forward (loc1)
- [x] Get Data In app: Proxmox REST, Cisco SSH (self-diagnosing), + methods overview
- [x] Host & infra metrics: UF `collect_host_metrics.sh` + Proxmox metrics → `*_metrics` (event indexes, timechart) + Host Metrics dashboard
- [x] UF distributed collection on ubuntu-london & ubuntu-berlin (`install-uf.sh <site>`), incl. `/var/log` file monitor
- [x] Alerts: CPU/mem >70% threshold + "forwarder stopped sending" (email js-csco@proton.me + Triggered Alerts + Alerts dashboard)
- [x] Asset Configuration: per-location KV Store inventory (RBAC-enforced) + automatic event enrichment + Add/Edit dashboard (Infrastructure Monitoring app)
- [x] Client → App → Hypervisor correlation: web-app LXC on Proxmox + in-container UF, ubuntu-berlin reachability probe, Windows UF (events + perfmon), and a 4-layer correlation dashboard
- [x] Save to GitHub: branch-per-save via KV queue + splunkd-context watcher (works around the search sandbox); apply.sh prompts for the token
- [x] Get Data In: "Indexes and Sourcetypes" explorer (tstats, RBAC-aware, click-to-search)
- [ ] **IT Service Intelligence (ITSI)** — premium, separately-licensed. Plan: (1) interim "Service Health" dashboard built from existing syslog + metrics (KPIs green/amber/red) to show the concept; (2) evaluate a scripted install of the ITSI package + a small service/KPI set (needs the package staged + a license).
- [ ] Remaining senders: Windows server (London → `london_windows`)
- [ ] SNMP via SC4SNMP; SOAP (parked)
