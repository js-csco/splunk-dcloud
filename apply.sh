#!/usr/bin/env bash
# ===========================================================================
# apply.sh - idempotent orchestrator that brings Splunk to the desired state.
#
# Because the dCloud VM resets to an empty template every session, this is
# written to build everything from scratch on each boot - but it is also
# safe to re-run on a persistent box (it reconciles rather than duplicates).
#
# v1 scope: deploy the lab app (indexes + demo dashboard).
# Later steps (roles, users, data integrations) plug in as new sections
# below without changing this structure.
# ===========================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- load config + helpers ------------------------------------------------
# shellcheck source=config/lab.env
source "${SCRIPT_DIR}/config/lab.env"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

CHANGED=0   # set to 1 by sync_dir when Splunk config actually changes

log "Applying dCloud Splunk lab config (app: ${LAB_APP})"

# --- sanity checks --------------------------------------------------------
[ -x "$(splunk_bin)" ] || die "Splunk not found at ${SPLUNK_HOME}. Set SPLUNK_HOME."

# ===========================================================================
# 1. Deploy Splunk apps (indexes, dashboards, and later props/inputs)
# ===========================================================================
log "Deploying app '${LAB_APP}' into \$SPLUNK_HOME/etc/apps ..."
sync_dir "${SCRIPT_DIR}/splunk/apps/${LAB_APP}" "${SPLUNK_HOME}/etc/apps/${LAB_APP}"
chown -R "${SPLUNK_USER}:${SPLUNK_USER}" "${SPLUNK_HOME}/etc/apps/${LAB_APP}" 2>/dev/null || true

# ===========================================================================
# 2. Roles          (placeholder - added in a later step)
# ===========================================================================
# Will ship as authorize.conf inside the app, so it's declarative too.

# ===========================================================================
# 3. Users          (placeholder - added in a later step)
# ===========================================================================
# Users don't live in app files cleanly, so these will be reconciled via the
# Splunk CLI here, guarded so re-runs are safe.

# ===========================================================================
# 4. Data integrations   (placeholder - added in a later step)
# ===========================================================================
# HEC tokens / forwarder inputs for the Ubuntu + Proxmox senders.

# ===========================================================================
# Apply changes
# ===========================================================================
if ! splunk_is_running; then
  log "Splunk is not running - starting it..."
  sudo -u "${SPLUNK_USER}" "$(splunk_bin)" start --accept-license --answer-yes --no-prompt
elif [ "${CHANGED}" = "1" ]; then
  log "Config changed - restarting Splunk to apply..."
  sudo -u "${SPLUNK_USER}" "$(splunk_bin)" restart
else
  log "No config changes detected - skipping restart."
fi

log "Done. Lab app '${LAB_APP}' is deployed."
log "Splunk UI: http://198.18.1.124:8000  (app: ${LAB_APP})"
