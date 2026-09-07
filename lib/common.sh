#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# common.sh - shared helpers sourced by apply.sh
# ---------------------------------------------------------------------------

# Consistent, timestamped logging.
log()  { printf '[%s] %s\n'  "$(date '+%H:%M:%S')" "$*"; }
warn() { printf '[%s] WARN: %s\n' "$(date '+%H:%M:%S')" "$*" >&2; }
die()  { printf '[%s] ERROR: %s\n' "$(date '+%H:%M:%S')" "$*" >&2; exit 1; }

# The splunk binary.
splunk_bin() { echo "${SPLUNK_HOME}/bin/splunk"; }

# Run a command as root when we aren't already (via sudo if needed).
as_root() {
  if [ "$(id -u)" = "0" ]; then "$@"
  elif command -v sudo >/dev/null 2>&1; then sudo "$@"
  else "$@"; fi
}

# Ensure DNS works. On dCloud, pod cloning remaps IPs and can leave the
# configured resolver unreachable - L3 egress still works (ping 1.1.1.1) but
# name resolution fails (ping google.com). If github.com can't be resolved,
# drop in a static public resolver. Idempotent: no-op when DNS already works.
fix_dns() {
  if getent hosts github.com >/dev/null 2>&1; then
    return 0
  fi
  warn "DNS cannot resolve github.com - installing static resolver (1.1.1.1/8.8.8.8)"
  as_root rm -f /etc/resolv.conf
  printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\noptions timeout:2 attempts:2\n' \
    | as_root tee /etc/resolv.conf >/dev/null
  getent hosts github.com >/dev/null 2>&1
}

# Run a command as the splunk user. If we're already that user (or sudo isn't
# available), run it directly. Works whether the bootstrap runs as root or not.
as_splunk() {
  if [ "$(id -un)" = "${SPLUNK_USER}" ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -u "${SPLUNK_USER}" "$@"
  else
    # No sudo and not the splunk user: try runuser (root), else run as-is.
    if command -v runuser >/dev/null 2>&1 && [ "$(id -u)" = "0" ]; then
      runuser -u "${SPLUNK_USER}" -- "$@"
    else
      "$@"
    fi
  fi
}

# Run a splunk CLI command as the splunk user, authenticated as admin.
splunk_cli() {
  as_splunk "$(splunk_bin)" "$@" \
    -auth "${SPLUNK_ADMIN_USER}:${SPLUNK_ADMIN_PASSWORD}"
}

# Is Splunk currently up?
splunk_is_running() {
  as_splunk "$(splunk_bin)" status 2>/dev/null | grep -qi 'is running'
}

# If Splunk is systemd-managed (boot-start), the CLI's start/restart drops to
# the splunk user and then calls systemctl, which triggers a polkit prompt and
# times out. Detect the unit so we can drive systemd directly as root instead.
# Echoes the unit name (e.g. "Splunkd") if managed, empty otherwise. Tries
# several detection methods because output/columns vary across systemd builds.
splunk_service_unit() {
  local u f d
  for u in Splunkd splunk SplunkForwarder; do
    # 'systemctl cat' exits 0 iff a unit file exists - most reliable probe.
    if systemctl cat "$u" >/dev/null 2>&1; then echo "$u"; return 0; fi
  done
  # Fallback: look for the unit file directly on disk.
  for d in /etc/systemd/system /lib/systemd/system /usr/lib/systemd/system; do
    for u in Splunkd splunk SplunkForwarder; do
      f="$d/$u.service"
      [ -f "$f" ] && { echo "$u"; return 0; }
    done
  done
  return 0
}

# Start Splunk the right way for this host (systemd if managed, else CLI).
splunk_start() {
  local unit; unit="$(splunk_service_unit)"
  if [ -n "$unit" ]; then
    log "Starting Splunk via systemd (${unit}.service)..."
    as_root systemctl start "$unit"
  else
    as_splunk "$(splunk_bin)" start --accept-license --answer-yes --no-prompt
  fi
}

# Restart Splunk the right way for this host.
splunk_restart() {
  local unit; unit="$(splunk_service_unit)"
  if [ -n "$unit" ]; then
    log "Restarting Splunk via systemd (${unit}.service)..."
    as_root systemctl restart "$unit"
  else
    as_splunk "$(splunk_bin)" restart
  fi
}

# Wait until an authenticated CLI call succeeds - this proves splunkd's mgmt
# port is up AND the admin credentials work, which is exactly what user
# reconciliation needs. Verified to return in ~1s once splunkd is ready.
wait_for_splunk() {
  local tries="${1:-45}" i=1
  while [ "$i" -le "$tries" ]; do
    if splunk_cli list user >/dev/null 2>&1; then
      return 0
    fi
    sleep 2; i=$((i+1))
  done
  return 1
}

# Copy a directory into place only if it differs, so we can tell whether a
# Splunk restart is actually needed. Sets CHANGED=1 on any change.
sync_dir() {
  local src="$1" dst="$2"
  mkdir -p "$dst"
  if command -v rsync >/dev/null 2>&1; then
    # Compare by content (checksum), ignoring owner/perms/times, so a re-run
    # with identical config doesn't trigger a needless Splunk restart.
    local out
    out="$(rsync -rlc --no-perms --no-owner --no-group --delete --itemize-changes "$src"/ "$dst"/)"
    # Use a full if - a bare "test && x" returns non-zero when the test is
    # false, which under 'set -e' would abort the script when there are no
    # changes to sync.
    if [ -n "$out" ]; then CHANGED=1; fi
  else
    cp -a "$src"/. "$dst"/
    CHANGED=1
  fi
}

# Create or update a Splunk user idempotently, mapped to a role.
# Tries 'add' first; if the user already exists, falls back to 'edit'. This
# avoids parsing 'list user' output, whose format varies across versions.
# Usage: ensure_user <username> <password> <role> <full name>
ensure_user() {
  local name="$1" pw="$2" role="$3" full="$4"
  if splunk_cli add user "${name}" -password "${pw}" -role "${role}" -full-name "${full}" >/dev/null 2>&1; then
    log "  created user '${name}' (role=${role})"
  else
    log "  user '${name}' exists - updating (role=${role})"
    splunk_cli edit user "${name}" -password "${pw}" -role "${role}" -full-name "${full}" >/dev/null
  fi
}
