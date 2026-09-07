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

# Wait until splunkd answers an authenticated REST call (or time out).
wait_for_splunk() {
  local tries="${1:-30}" i=1
  while [ "$i" -le "$tries" ]; do
    if splunk_cli rest --quiet /services/server/info >/dev/null 2>&1; then
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
    local out
    out="$(rsync -a --itemize-changes --delete "$src"/ "$dst"/)"
    [ -n "$out" ] && CHANGED=1
  else
    cp -a "$src"/. "$dst"/
    CHANGED=1
  fi
}

# Create or update a Splunk user idempotently, mapped to a role.
# Usage: ensure_user <username> <password> <role> <full name>
ensure_user() {
  local name="$1" pw="$2" role="$3" full="$4"
  if splunk_cli list user 2>/dev/null | grep -Eq "^[[:space:]]*${name}[[:space:]]*$|^[[:space:]]*${name}:"; then
    log "  user '${name}' exists - reconciling role=${role}"
    splunk_cli edit user "${name}" -password "${pw}" -role "${role}" -full-name "${full}" >/dev/null
  else
    log "  creating user '${name}' (role=${role})"
    splunk_cli add user "${name}" -password "${pw}" -role "${role}" -full-name "${full}" >/dev/null
  fi
}
