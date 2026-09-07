#!/usr/bin/env bash
# ===========================================================================
# apply.sh - idempotent orchestrator that brings Splunk to the desired state.
#
# The dCloud VM resets to an empty template every session, so this builds
# everything from scratch on each boot - but it is also safe to re-run on a
# persistent box (it reconciles rather than duplicates).
#
# Order matters:
#   1. deploy app files (indexes.conf, authorize.conf/roles, dashboards)
#   2. start/restart Splunk so those declarative configs take effect
#   3. reconcile users via the CLI (needs splunkd up AND roles to exist first)
# ===========================================================================
set -euo pipefail

# Never fail silently: report the line where an errexit aborts the script.
trap 'rc=$?; [ "$rc" -ne 0 ] && echo "[apply.sh] aborted (exit $rc) at line ${LINENO}" >&2' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- load config + helpers ------------------------------------------------
# shellcheck source=config/lab.env
source "${SCRIPT_DIR}/config/lab.env"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

CHANGED=0   # set to 1 by sync_dir when Splunk config actually changes

# Self-heal DNS in case this is run directly on a pod with a broken resolver.
fix_dns || warn "DNS still not resolving - later network steps may fail."

log "Applying dCloud Splunk lab config (app: ${LAB_APP})"

# --- sanity checks --------------------------------------------------------
[ -x "$(splunk_bin)" ] || die "Splunk not found at ${SPLUNK_HOME}. Set SPLUNK_HOME."

# ===========================================================================
# 1. Deploy the Splunk app: indexes + roles (authorize.conf) + dashboards
# ===========================================================================
log "Deploying app '${LAB_APP}' into \$SPLUNK_HOME/etc/apps ..."
sync_dir "${SCRIPT_DIR}/splunk/apps/${LAB_APP}" "${SPLUNK_HOME}/etc/apps/${LAB_APP}"
chown -R "${SPLUNK_USER}:${SPLUNK_USER}" "${SPLUNK_HOME}/etc/apps/${LAB_APP}" 2>/dev/null || true

# ===========================================================================
# 2. Apply declarative config (start Splunk, or restart if config changed)
# ===========================================================================
if ! splunk_is_running; then
  log "Splunk is not running - starting it..."
  splunk_start
elif [ "${CHANGED}" = "1" ]; then
  log "Config changed - restarting Splunk to apply indexes/roles..."
  splunk_restart
else
  log "No config changes detected - skipping restart."
fi

log "Waiting for splunkd to become ready..."
wait_for_splunk 45 || die "splunkd did not become ready in time."

# ---------------------------------------------------------------------------
# Roles - created at runtime via REST (immediate, restart-independent). This
# is the authoritative source for the lab's RBAC roles.
#   role_loc1  -> loc1_* only
#   role_loc2  -> loc2_* only
#   role_global-> all locations (+ internal indexes for the health dashboard)
# ---------------------------------------------------------------------------
log "Reconciling RBAC roles via REST ..."
rfails=0
ensure_role role_loc1   "user" "loc1_*"                   "loc1_*"                || rfails=$((rfails+1))
ensure_role role_loc2   "user" "loc2_*"                   "loc2_*"                || rfails=$((rfails+1))
ensure_role role_global "user" "loc1_*;loc2_*;loc3_*;_*"  "loc1_*;loc2_*;loc3_*"  || rfails=$((rfails+1))
[ "${rfails}" -eq 0 ] || warn "${rfails} role(s) failed to reconcile - see errors above."

# ===========================================================================
# 3. Reconcile lab users (CLI; roles from step 1 already exist by now)
# ===========================================================================
USERS_CSV="${SCRIPT_DIR}/config/lab_users.csv"
if [ -f "${USERS_CSV}" ]; then
  log "Reconciling lab users from $(basename "${USERS_CSV}") ..."
  # Skip comment lines and the header row; tolerate spaces after commas.
  fails=0
  while IFS=',' read -r username password role full_name; do
    case "${username}" in ''|\#*|username) continue ;; esac
    ensure_user "${username// /}" "${password// /}" "${role// /}" "${full_name}" || fails=$((fails+1))
  done < "${USERS_CSV}"
  [ "${fails}" -eq 0 ] || warn "${fails} user(s) failed to reconcile - see errors above."
else
  warn "No ${USERS_CSV} found - skipping user creation."
fi

# ===========================================================================
# 4. Save-to-GitHub button: persist the PAT (if provided) for labsync.sh
# ===========================================================================
# The token is stored OUTSIDE the app dir so it is never captured/committed by
# the snapshot. It must be provided at session start via the GITHUB_TOKEN env
# var (e.g. in the dCloud startup command), since the VM wipes each session.
TOKEN_DIR="${SPLUNK_HOME}/var/lib/dcloud"
if [ -n "${GITHUB_TOKEN:-}" ]; then
  mkdir -p "${TOKEN_DIR}"
  printf '%s' "${GITHUB_TOKEN}" > "${TOKEN_DIR}/gh.token"
  chmod 700 "${TOKEN_DIR}"; chmod 600 "${TOKEN_DIR}/gh.token"
  chown -R "${SPLUNK_USER}:${SPLUNK_USER}" "${TOKEN_DIR}" 2>/dev/null || true
  log "Save-to-GitHub: token stored (button is active)."
else
  log "Save-to-GitHub: no GITHUB_TOKEN provided - button will report 'no token' until one is set."
fi
# Make the labsync scripts executable in the deployed app.
chmod +x "${SPLUNK_HOME}/etc/apps/${LAB_APP}/bin/"*.sh 2>/dev/null || true

# ===========================================================================
# 5. Data integrations   (placeholder - added in a later step)
# ===========================================================================
# HEC tokens / forwarder inputs for the Ubuntu + Proxmox senders.

log "Done. Lab app '${LAB_APP}' deployed with per-location indexes, roles, and users."
log "Splunk UI: http://198.18.1.124:8000  (app: ${LAB_APP})"
