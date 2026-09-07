# splunk-dcloud

GitHub-driven, self-rebuilding **Splunk RBAC lab** for Cisco **dCloud**. The
dCloud VM resets to an empty template each session, so on every startup one
command pulls this repo and rebuilds the entire Splunk configuration from
scratch — per-location indexes, RBAC roles, users, and dashboards. Nothing is
configured by hand; the lab is identical every time.

To change the lab, edit files here and `git push` to `main` — the next session
picks it up automatically. The dCloud startup command never changes.

![Lab topology](splunk/apps/dcloud_lab/appserver/static/topology.svg)

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

## The one command (dCloud Startup Automation)

dCloud pods often boot with a **broken DNS resolver** (pod cloning remaps IPs,
leaving the configured nameserver unreachable — `ping 1.1.1.1` works but
`ping google.com` fails). Because the resolver must be fixed *before* the repo
can be fetched, the startup command is self-contained: it fixes DNS, ensures
`git`, clones `main`, and runs `apply.sh`:

```bash
sudo bash -c 'getent hosts github.com >/dev/null 2>&1 || { rm -f /etc/resolv.conf; printf "nameserver 1.1.1.1\nnameserver 8.8.8.8\n" > /etc/resolv.conf; }; command -v git >/dev/null || { apt-get update -y && apt-get install -y git; }; rm -rf /opt/dcloud-splunk; git clone -b main https://github.com/js-csco/splunk-dcloud.git /opt/dcloud-splunk && exec bash /opt/dcloud-splunk/apply.sh'
```

> **Always clone with `-b main`.** A plain `git clone` pulls the repo's
> *default* branch, which may not be `main` — pinning the branch avoids
> running stale code.

### If DNS is broken and you just want to unblock a shell

```bash
sudo rm -f /etc/resolv.conf
printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\noptions timeout:2 attempts:2\n' | sudo tee /etc/resolv.conf
getent hosts github.com   # should print an IP
```

## Location-based RBAC (the core design)

Access control is enforced at the **index** level. The golden rule: an index
belongs to exactly ONE location, so a role granting one location's indexes can
never leak another's. Indexes are named `loc<N>_<datatype>` so a role grants a
whole location with a single wildcard.

| Location | Network | Devices | Indexes | Roles with access |
|---|---|---|---|---|
| loc1 | 198.18.1.0/24 | splunk | `loc1_linux` | `role_loc1`, `role_global` |
| loc2 | 198.18.2.0/24 | ubuntu-loc2, windows-server-2022-loc2, cisco-iq-link | `loc2_linux`, `loc2_windows`, `loc2_network` | `role_loc2`, `role_global` |
| loc3 | 198.18.3.0/24 | proxmox-9.2-loc3, ubuntu-loc3 | `loc3_linux`, `loc3_proxmox` | `role_global` |

**Roles** (created at runtime via REST in `apply.sh`): `role_loc1 → loc1_*`,
`role_loc2 → loc2_*`, `role_global → loc1_*;loc2_*;loc3_*` (+ `_*` for the
health dashboard). REST is used instead of `authorize.conf` because on this
image app-level `authorize.conf` roles did not register even after a restart,
whereas REST creation is immediate and reliable.

**Users** (`config/lab_users.csv`, created at boot):

| User | Role | Sees |
|---|---|---|
| `user_global` | `role_global` | all locations |
| `user_loc1` | `role_loc1` | Location 1 only |
| `user_loc2` | `role_loc2` | Location 2 only |

> Location 3 has no dedicated user yet, so its data is **global-only**. Add a
> `role_loc3` + `user_loc3` later exactly like the others.

> Demo passwords live in `config/lab_users.csv` and are intended for a
> throwaway lab. Do not put real passwords there.

## Dashboards (in the "Lab Overview" app)

| Dashboard | Purpose |
|---|---|
| **Lab Info** (landing page) | What the lab is, the topology diagram, repo link, Splunk version, and the deploy command. |
| **Setup Status** | Post-deploy verification — green/red checklist confirming indexes, roles, users, and the app all loaded before the demo starts. |
| **Ingestion & Health** | Event volume per location index and Splunk health. |

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
    data/ui/views/lab_info.xml       # landing page
    data/ui/views/setup.xml          # setup verification
    data/ui/views/lab_overview.xml   # ingestion & health
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
- [x] RBAC roles (created via REST) + users mapped to them
- [x] Dashboards: Lab Info, Setup Status, Ingestion & Health
- [ ] `role_loc3` / `user_loc3` (if/when Location 3 needs its own user)
- [ ] Data integrations: forwarders / HEC / syslog for the Ubuntu VMs and Proxmox
