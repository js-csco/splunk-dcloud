#!/usr/bin/env python3
# ===========================================================================
# proxmox_heal.py - custom alert action: self-healing for the demo.
#
# When a container's Reachability drops, the triggering search passes the
# container's VMID; this action SSHes to the Proxmox host and runs
# `pct start <vmid>` to bring it back. Paired with the ITSI Episode, it gives
# the "kill it -> ITSI notices -> ITSI heals it -> Episode closes" story.
#
# Proxmox connection is read (out of repo) from the same files the poller uses:
#   get_data_in/bin/proxmox_config.env  (committed lab defaults)
#   $SPLUNK_HOME/var/lib/dcloud/proxmox.env   (runtime override)
# Keys: PROXMOX_HOST, PROXMOX_USER (root@pam -> ssh user root), PROXMOX_PASSWORD.
#
# Splunk runs it as:  proxmox_heal.py --execute   (JSON payload on stdin)
# The VMID comes from the result field 'vmid' via param.vmid = $result.vmid$.
# ===========================================================================
import json
import os
import subprocess
import sys

SPLUNK_HOME = os.environ.get("SPLUNK_HOME", "/opt/splunk")
CFG_FILES = [
    os.path.join(SPLUNK_HOME, "etc", "apps", "get_data_in", "bin", "proxmox_config.env"),
    os.path.join(SPLUNK_HOME, "var", "lib", "dcloud", "proxmox.env"),
]


def load_cfg():
    cfg = {}
    for path in CFG_FILES:
        try:
            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip().strip('"').strip("'")
        except OSError:
            pass
    return cfg


def main():
    if len(sys.argv) < 2 or sys.argv[1] != "--execute":
        sys.stderr.write("proxmox_heal: expected --execute\n")
        return 2
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("proxmox_heal: bad payload: %s\n" % exc)
        return 2

    cfg_action = payload.get("configuration", {}) or {}
    vmid = str(cfg_action.get("vmid", "")).strip()
    if not vmid.isdigit():
        sys.stderr.write("proxmox_heal: no numeric vmid (got %r) - set "
                         "action.proxmox_heal.param.vmid = $result.vmid$\n" % vmid)
        return 2

    cfg = load_cfg()
    host = cfg.get("PROXMOX_HOST")
    pw = cfg.get("PROXMOX_PASSWORD")
    user = (cfg.get("PROXMOX_USER") or "root@pam").split("@")[0] or "root"
    if not host or not pw:
        sys.stderr.write("proxmox_heal: missing PROXMOX_HOST / PROXMOX_PASSWORD in proxmox.env\n")
        return 2

    cmd = ["sshpass", "-p", pw, "ssh",
           "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
           "-o", "ConnectTimeout=10", "%s@%s" % (user, host),
           "pct status %s | grep -q running || pct start %s" % (vmid, vmid)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        sys.stderr.write("proxmox_heal: vmid=%s rc=%s out=%r err=%r\n"
                         % (vmid, r.returncode, r.stdout.strip(), r.stderr.strip()))
        return 0 if r.returncode == 0 else 3
    except FileNotFoundError:
        sys.stderr.write("proxmox_heal: sshpass not installed on the Splunk host\n")
        return 3
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("proxmox_heal: ssh failed: %s\n" % exc)
        return 3


if __name__ == "__main__":
    sys.exit(main())
