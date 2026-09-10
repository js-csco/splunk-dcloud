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

# --- tools for scripted inputs (SSH polling of network devices) -----------
if ! command -v sshpass >/dev/null 2>&1; then
  log "Installing sshpass (for the SSH device scripted input)..."
  export DEBIAN_FRONTEND=noninteractive
  { apt-get update -y && apt-get install -y sshpass; } >/dev/null 2>&1 \
    || warn "sshpass install failed - SSH device polling will report an error until it's installed."
fi

# ===========================================================================
# 1. Deploy every Splunk app under splunk/apps/ (indexes, dashboards,
#    receivers, etc.)
# ===========================================================================
log "Deploying Splunk apps into \$SPLUNK_HOME/etc/apps ..."
for app_src in "${SCRIPT_DIR}"/splunk/apps/*/; do
  [ -d "$app_src" ] || continue
  app_name="$(basename "$app_src")"
  log "  app: ${app_name}"
  sync_dir "$app_src" "${SPLUNK_HOME}/etc/apps/${app_name}"
  chown -R "${SPLUNK_USER}:${SPLUNK_USER}" "${SPLUNK_HOME}/etc/apps/${app_name}" 2>/dev/null || true
done

# --- default landing + home dashboard for ALL users -----------------------
# Written BEFORE the start/restart below so Splunk reads it at (re)start.
# [general_default] = org-wide defaults for users who haven't set their own.
#   default_namespace           -> which app opens after login
#   display.page.home.dashboardId -> the Home page's "Dashboard" tab
# Written directly (not via app sync, which uses --delete and would clobber the
# system user-prefs app). If the content changes, force a restart so it applies.
UP_DIR="${SPLUNK_HOME}/etc/apps/user-prefs/local"
mkdir -p "${UP_DIR}"
UP_TMP="$(mktemp)"
cat > "${UP_TMP}" <<'UPEOF'
# Managed by splunk-dcloud/apply.sh - default app + home dashboard for all users.
[general_default]
default_namespace = dcloud_lab
display.page.home.dashboardId = /servicesNS/nobody/dcloud_lab/data/ui/views/lab_info
UPEOF
if ! cmp -s "${UP_TMP}" "${UP_DIR}/user-prefs.conf" 2>/dev/null; then
  cp "${UP_TMP}" "${UP_DIR}/user-prefs.conf"
  chown -R "${SPLUNK_USER}:${SPLUNK_USER}" "${SPLUNK_HOME}/etc/apps/user-prefs" 2>/dev/null || true
  CHANGED=1
  log "Default landing + home dashboard set to Lab Info for all users."
fi
rm -f "${UP_TMP}"

# --- raise Splunk Web upload limit ----------------------------------------
# The UI "Install app from file" caps uploads at ~500 MB; big apps (ITSI) exceed
# that. Raise it to 1 GB. (CLI 'splunk install app' has no such limit anyway.)
WEBCONF="${SPLUNK_HOME}/etc/system/local/web.conf"
if grep -q 'max_upload_size' "${WEBCONF}" 2>/dev/null; then
  :
elif grep -q '^\[settings\]' "${WEBCONF}" 2>/dev/null; then
  sed -i '/^\[settings\]/a max_upload_size = 1024' "${WEBCONF}"; CHANGED=1
  log "Raised Splunk Web max_upload_size to 1024 MB."
else
  printf '\n[settings]\nmax_upload_size = 1024\n' >> "${WEBCONF}"; CHANGED=1
  log "Raised Splunk Web max_upload_size to 1024 MB."
fi

# --- Splunkbase apps (MCP Server, etc.) -----------------------------------
# Opt-in only, and NON-interactive (no prompt at boot). To install a Splunkbase
# app, provide splunk.com creds as env vars when running apply.sh, e.g.:
#   sudo SPLUNKBASE_USERNAME='you@example.com' SPLUNKBASE_PASSWORD='...' \
#        SPLUNKBASE_APP_IDS='7931' bash apply.sh
# Prefer SPLUNK_INSTALL_URLS below for a URL-hosted .spl (no splunk.com account).
if [ -n "${SPLUNKBASE_USERNAME:-}" ] && [ -n "${SPLUNKBASE_PASSWORD:-}" ]; then
  SB_APP_IDS="${SPLUNKBASE_APP_IDS:-7931}"
  log "Installing Splunkbase app(s) [${SB_APP_IDS}] from splunk.com ..."
  if python3 "${SCRIPT_DIR}/lib/splunkbase_install.py" \
       "${SPLUNKBASE_USERNAME}" "${SPLUNKBASE_PASSWORD}" "${SPLUNK_HOME}/etc/apps" ${SB_APP_IDS}; then
    chown -R "${SPLUNK_USER}:${SPLUNK_USER}" "${SPLUNK_HOME}/etc/apps" 2>/dev/null || true
    CHANGED=1
  else
    warn "Splunkbase install failed (creds/entitlement/network?)."
  fi
fi

# --- Install apps from direct URLs (ITSI, MCP, any .spl/.tgz) --------------
# Guaranteed no-file-move path that needs no Splunkbase entitlement/terms: host
# the package at a URL the lab can reach (your own file host, a temporary signed
# link, an internal server) and it is fetched + extracted server-side, before
# Splunk starts. Nothing is committed to the repo. Space-separated in
# SPLUNK_INSTALL_URLS, or entered interactively. .spl and .tgz are both gzip
# tarballs, so extraction handles either.
if [ -z "${SPLUNK_INSTALL_URLS:-}" ] && [ -r /dev/tty ]; then
  printf 'Direct URL(s) of .spl/.tgz apps to install (ITSI/others; space-separated; blank to skip): ' > /dev/tty
  IFS= read -r SPLUNK_INSTALL_URLS < /dev/tty || true
fi
if [ -n "${SPLUNK_INSTALL_URLS:-}" ]; then
  for _u in ${SPLUNK_INSTALL_URLS}; do
    _tmp="$(mktemp /tmp/appdl.XXXXXX.tgz)"
    log "Fetching app package: ${_u}"
    if curl -fsSL --retry 3 -o "${_tmp}" "${_u}"; then
      if tar -xzf "${_tmp}" -C "${SPLUNK_HOME}/etc/apps" 2>/dev/null; then
        log "  installed app package from URL"
        CHANGED=1
      else
        warn "  could not extract package (not a .spl/.tgz gzip tarball?)"
      fi
    else
      warn "  download failed for one URL (unreachable / needs auth?)."
    fi
    rm -f "${_tmp}"
  done
  chown -R "${SPLUNK_USER}:${SPLUNK_USER}" "${SPLUNK_HOME}/etc/apps" 2>/dev/null || true
fi

# --- ITSI prerequisite: Java --------------------------------------------------
# ITSI (installed by extracting its .spl into etc/apps - the ONLY supported
# method; NOT Splunk Web, NOT `splunk install app`) needs a JRE for anomaly
# detection + notable-event management. ITSI supports Java 8-11 or 17, so pin
# openjdk-17 (Ubuntu 24.04's default JRE is 21, which ITSI does not support).
# Runs before the restart below so ITSI starts with Java on PATH.
if [ -d "${SPLUNK_HOME}/etc/apps/itsi" ]; then
  if command -v java >/dev/null 2>&1 && java -version 2>&1 | grep -Eq '"(1\.8|9|1[01]|17)'; then
    log "ITSI: supported Java already present ($(java -version 2>&1 | head -1))."
  elif command -v apt-get >/dev/null 2>&1; then
    log "ITSI detected - installing OpenJDK 17 (required for anomaly/notable features)..."
    export DEBIAN_FRONTEND=noninteractive
    if apt-get update -y >/dev/null 2>&1 && apt-get install -y openjdk-17-jre-headless >/dev/null 2>&1; then
      log "  installed $(java -version 2>&1 | head -1)"
    else
      warn "  could not auto-install Java; install openjdk-17-jre-headless and set JAVA_HOME for ITSI."
    fi
  else
    warn "ITSI detected but no supported Java and no apt-get; install a Java 8-11/17 JRE for ITSI."
  fi
fi

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
#   role_london -> london_* only        (Location 2)
#   role_berlin -> berlin_* only        (Location 3)
#   role_global -> London + Berlin + loc1 infra + internal (everything)
# Roles get EXPLICIT capabilities and do NOT import the built-in 'user' role
# (which grants srchIndexesAllowed=* and would leak every index).
# ---------------------------------------------------------------------------
LOC_CAPS="search;rtsearch;get_metadata;get_typeahead;schedule_search;edit_own_objects;list_metrics_catalog"
log "Reconciling RBAC roles via REST ..."
rfails=0
ensure_role role_london "" "london_*"                    "london_*"                 "$LOC_CAPS" || rfails=$((rfails+1))
ensure_role role_berlin "" "berlin_*"                    "berlin_*"                 "$LOC_CAPS" || rfails=$((rfails+1))
ensure_role role_global "" "london_*;berlin_*;loc1_*;_*" "london_*;berlin_*;loc1_*" "$LOC_CAPS" || rfails=$((rfails+1))
[ "${rfails}" -eq 0 ] || warn "${rfails} role(s) failed to reconcile - see errors above."

# Prune retired RBAC objects from earlier naming schemes. Safe if absent.
# Keeps re-runs on a live box clean (fresh sessions never have them).
ROLES_BASE="${SPLUNK_MGMT_URI:-https://127.0.0.1:8089}/services/authorization/roles"
USERS_BASE="${SPLUNK_MGMT_URI:-https://127.0.0.1:8089}/services/authentication/users"
for legacy_user in user_loc1 user_loc2 user_loc3 user_global; do
  splunk_rest -o /dev/null -X DELETE "${USERS_BASE}/${legacy_user}" >/dev/null 2>&1 || true
done
for legacy_role in role_loc1 role_loc2 role_loc3; do
  splunk_rest -o /dev/null -X DELETE "${ROLES_BASE}/${legacy_role}" >/dev/null 2>&1 || true
done

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
GH_TOKEN_FILE="${TOKEN_DIR}/gh.token"
# If not supplied via env, prompt for it interactively (read from the terminal so
# it works even when apply.sh is piped via `curl | bash`). The token is never
# echoed. Blank keeps any existing token; if none, the Save button stays inactive.
if [ -z "${GITHUB_TOKEN:-}" ] && [ -r /dev/tty ]; then
  printf 'GitHub token for "Save to GitHub" (fine-grained PAT, Contents: Read+Write; blank to skip): ' > /dev/tty
  IFS= read -rs GITHUB_TOKEN < /dev/tty || true
  printf '\n' > /dev/tty
fi
if [ -n "${GITHUB_TOKEN:-}" ]; then
  mkdir -p "${TOKEN_DIR}"
  printf '%s' "${GITHUB_TOKEN}" > "${GH_TOKEN_FILE}"
  chmod 700 "${TOKEN_DIR}"; chmod 600 "${GH_TOKEN_FILE}"
  chown -R "${SPLUNK_USER}:${SPLUNK_USER}" "${TOKEN_DIR}" 2>/dev/null || true
  log "Save-to-GitHub: token stored (button is active)."
elif [ -s "${GH_TOKEN_FILE}" ]; then
  log "Save-to-GitHub: keeping existing token on the box."
else
  log "Save-to-GitHub: no token provided - button will report 'no token' until one is set."
fi

# Get Data In: persist Proxmox API creds (if provided) for the REST poller.
# Stored outside any app dir; the poll_proxmox.py scripted input reads it.
# Only needed to OVERRIDE the committed lab defaults (proxmox_config.env).
if [ -n "${PROXMOX_HOST:-}" ] || [ -n "${PROXMOX_TOKEN:-}" ] || [ -n "${PROXMOX_USER:-}" ]; then
  mkdir -p "${TOKEN_DIR}"
  {
    [ -n "${PROXMOX_HOST:-}" ]     && echo "PROXMOX_HOST=${PROXMOX_HOST}"
    [ -n "${PROXMOX_PORT:-}" ]     && echo "PROXMOX_PORT=${PROXMOX_PORT}"
    [ -n "${PROXMOX_USER:-}" ]     && echo "PROXMOX_USER=${PROXMOX_USER}"
    [ -n "${PROXMOX_PASSWORD:-}" ] && echo "PROXMOX_PASSWORD=${PROXMOX_PASSWORD}"
    [ -n "${PROXMOX_TOKEN:-}" ]    && echo "PROXMOX_TOKEN=${PROXMOX_TOKEN}"
  } > "${TOKEN_DIR}/proxmox.env"
  chmod 700 "${TOKEN_DIR}"; chmod 600 "${TOKEN_DIR}/proxmox.env"
  chown -R "${SPLUNK_USER}:${SPLUNK_USER}" "${TOKEN_DIR}" 2>/dev/null || true
  log "Get Data In: Proxmox override creds stored."
else
  log "Get Data In: using committed Proxmox config (proxmox_config.env)."
fi

# Make any deployed app bin scripts executable.
chmod +x "${SPLUNK_HOME}/etc/apps/"*/bin/*.sh 2>/dev/null || true

# ===========================================================================
# 5. Feed the Splunk host's own logs into loc1_linux
# ===========================================================================
# The Splunk server IS the Location 1 device, so forward its OS logs to the
# loc1 receiver (127.0.0.1:5513 -> loc1_linux). Reuses the same rsyslog path as
# the Ubuntu senders and runs as root, so there are no file-permission issues.
if command -v rsyslogd >/dev/null 2>&1; then
  RS_CONF="/etc/rsyslog.d/99-splunk-dcloud-loc1.conf"
  cat > "${RS_CONF}" <<'RSEOF'
# Managed by splunk-dcloud/apply.sh
# Forward this host's (loc1) logs to Splunk -> index loc1_linux
*.* @@127.0.0.1:5513
RSEOF
  systemctl restart rsyslog 2>/dev/null || service rsyslog restart 2>/dev/null || true
  logger "dcloud-splunk: loc1 self-forwarding enabled (this host -> loc1_linux)" 2>/dev/null || true
  log "loc1 self-forwarding configured (this host -> loc1_linux)."
else
  warn "rsyslog not present - loc1_linux will have no data."
fi

# ===========================================================================
# 6. Seed the Asset Configuration inventory (KV Store) from the committed CSV
# ===========================================================================
# One KV collection per location (RBAC). Idempotent: outputlookup append=true
# with _key=host UPSERTS, so re-runs update rather than duplicate. Retries in
# case the KV store isn't fully initialised right after a start/restart.
ADMIN_PW="${SPLUNK_ADMIN_PASSWORD:-C1sco12345}"
run_splunk() { if [ "$(id -u)" = "0" ]; then sudo -u "${SPLUNK_USER}" "$@"; else "$@"; fi; }
log "Seeding Asset Configuration inventory (KV Store) ..."
for site in loc1 london berlin; do
  seeded=0
  for attempt in 1 2 3 4 5; do
    if run_splunk "$(splunk_bin)" search \
        "| inputlookup assets_seed | search site=${site} | eval _key=host | outputlookup dcloud_assets_${site}_lk append=true" \
        -app infra_monitoring -auth "admin:${ADMIN_PW}" -maxout 0 >/dev/null 2>&1; then
      seeded=1; break
    fi
    sleep 3
  done
  [ "${seeded}" -eq 1 ] && log "  asset inventory seeded: ${site}" || warn "  asset seed failed: ${site} (KV store not ready?)"
done

# ===========================================================================
# 7. Data integrations   (placeholder - added in a later step)
# ===========================================================================
# HEC tokens / forwarder inputs for the Ubuntu + Proxmox senders.

log "Done. Lab app '${LAB_APP}' deployed with per-location indexes, roles, and users."
log "Splunk UI: http://198.18.1.124:8000  (app: ${LAB_APP})"
