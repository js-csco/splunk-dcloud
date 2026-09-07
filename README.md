# splunk-dcloud

GitHub-driven Splunk lab deployment for Cisco **dCloud**. On every session
startup, the dCloud VM runs one command that pulls this repo and applies the
full Splunk configuration from scratch — indexes, apps, dashboards, and
(later) roles, users, and data integrations.

Because dCloud resets the VM to an empty template each session, the whole lab
is defined here as code and rebuilt on each boot. To change the lab, edit
files here and `git push` — the dCloud startup command never changes.

---

## How it works

```
dCloud Startup Automation (session.xml)
        │  one command
        ▼
   bootstrap.sh      clones/refreshes this repo onto the VM, then runs:
        ▼
   apply.sh          idempotent orchestrator
        ├── deploys the Splunk app (indexes + dashboards) into $SPLUNK_HOME/etc/apps
        ├── [later] roles        (authorize.conf in the app)
        ├── [later] users        (Splunk CLI, reconciled)
        ├── [later] data inputs  (HEC / forwarder inputs for Ubuntu + Proxmox)
        └── starts/restarts Splunk only if something changed
```

## The one command (dCloud Startup Automation)

Point dCloud's Startup Automation at a single line:

```bash
curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/claude/splunk-lab-deployment-u5s91k/bootstrap.sh | sudo bash
```

Once this branch is merged to `main`, swap the branch in that URL for `main`.
You can also override defaults inline, e.g.:

```bash
DCLOUD_BRANCH=main curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/bootstrap.sh | sudo bash
```

## Repo layout

```
bootstrap.sh                  # entry point dCloud calls (clone + hand off)
apply.sh                      # idempotent orchestrator (all the real logic)
config/lab.env                # tunables (paths, repo/branch, app name, creds source)
lib/common.sh                 # shared shell helpers
splunk/apps/dcloud_lab/       # the Splunk app that gets deployed
  default/
    app.conf                  # app manifest
    indexes.conf              # lab indexes (linux_logs, proxmox, network, demo)
    data/ui/nav/default.xml   # app navigation
    data/ui/views/lab_overview.xml   # demo dashboard
  metadata/default.meta       # sharing/permissions
```

## What v1 deploys

- **Indexes:** `linux_logs`, `proxmox`, `network`, `demo` — declarative in
  `indexes.conf`, created on Splunk restart.
- **App + demo dashboard:** the `dcloud_lab` app with a **Lab Overview**
  dashboard (event counts per index, ingestion over time, Splunk health).

## Environment assumptions

- Cisco dCloud VM, Ubuntu 24.04, Splunk **10.4.0** already installed and
  running.
- Splunk UI at `http://198.18.1.124:8000`, admin login already provisioned.
- The startup command runs with root privileges (via `sudo`).

## Configuration

Edit `config/lab.env` for paths and the app name. Credentials default to the
dCloud demo values but should be overridden via the environment for anything
sensitive — **do not commit real secrets**.

## Roadmap

- [x] v1: indexes + lab app with demo dashboard
- [ ] Roles (declarative `authorize.conf`)
- [ ] Users (reconciled via Splunk CLI)
- [ ] Data integrations: HEC + forwarder inputs for the Ubuntu VMs and Proxmox
