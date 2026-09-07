# splunk-dcloud

GitHub-driven Splunk lab deployment for Cisco **dCloud**. On every session
startup, the dCloud VM runs one command that pulls this repo and applies the
full Splunk configuration from scratch — per-location indexes, RBAC roles,
users, an app with a dashboard, and (later) data integrations.

Because dCloud resets the VM to an empty template each session, the whole lab
is defined here as code and rebuilt on each boot. To change the lab, edit
files here and `git push` to `main` — the dCloud startup command never changes.

---

## How it works

```
dCloud Startup Automation (session.xml)
        │  one command
        ▼
   bootstrap.sh      clones/refreshes this repo onto the VM, then runs:
        ▼
   apply.sh          idempotent orchestrator
        ├── deploys the dcloud_lab app (indexes.conf + authorize.conf + dashboard)
        ├── starts/restarts Splunk so those declarative configs take effect
        ├── reconciles lab users via the Splunk CLI (mapped to roles)
        └── [later] data inputs (HEC / forwarder inputs for Ubuntu + Proxmox)
```

## The one command (dCloud Startup Automation)

Point dCloud's Startup Automation at a single line:

```bash
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/bootstrap.sh | sudo bash
```

(`sudo` is optional — the scripts also run fine as root.) You can override
defaults inline, e.g. `DCLOUD_BRANCH=some-branch curl ... | bash`.

## Location-based RBAC (the core design)

Access control is enforced at the **index** level. The golden rule: an index
belongs to exactly ONE location, so a role granting one location's indexes can
never leak another's. Indexes are named `loc<N>_<datatype>` so a role grants a
whole location with a single wildcard.

| Location | Network | Devices | Indexes | Roles with access |
|---|---|---|---|---|
| loc1 | 198.18.1.0/24 | splunk | `loc1_linux` | `role_loc1`, `role_global` |
| loc2 | 198.18.2.0/24 | ubuntu-loc2, windows-server-2022, cisco-iq-link | `loc2_linux`, `loc2_windows`, `loc2_network` | `role_loc2`, `role_global` |
| loc3 | 198.18.3.0/24 | proxmox-9.2, ubuntu-loc3 | `loc3_linux`, `loc3_proxmox` | `role_global` |

**Roles** (`authorize.conf`): `role_loc1 → loc1_*`, `role_loc2 → loc2_*`,
`role_global → loc1_*;loc2_*;loc3_*`.

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

## Repo layout

```
bootstrap.sh                  # entry point dCloud calls (clone + hand off)
apply.sh                      # idempotent orchestrator (all the real logic)
config/lab.env                # tunables (paths, repo/branch, app name, creds source)
config/lab_users.csv          # lab users -> roles (demo passwords)
lib/common.sh                 # shared shell helpers
splunk/apps/dcloud_lab/
  default/
    app.conf                  # app manifest
    indexes.conf              # per-location indexes
    authorize.conf            # per-location RBAC roles
    data/ui/nav/default.xml   # app navigation
    data/ui/views/lab_overview.xml   # demo dashboard
  metadata/default.meta       # sharing/permissions
```

## Environment assumptions

- Cisco dCloud VM, Ubuntu 24.04, Splunk **10.4.0** already installed/running.
- Splunk UI at `http://198.18.1.124:8000`, admin login already provisioned.
- The startup command runs as root (or with sudo).

## Roadmap

- [x] Per-location indexes + lab app with demo dashboard
- [x] RBAC roles (declarative `authorize.conf`)
- [x] Users mapped to roles (reconciled via Splunk CLI)
- [ ] `role_loc3` / `user_loc3` (if/when Location 3 needs its own user)
- [ ] Data integrations: HEC + forwarder inputs for the Ubuntu VMs and Proxmox
