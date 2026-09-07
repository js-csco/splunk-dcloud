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

# Run a splunk CLI command as the splunk user, authenticated as admin.
# Usage: splunk_cli <args...>
splunk_cli() {
  sudo -u "${SPLUNK_USER}" "$(splunk_bin)" "$@" \
    -auth "${SPLUNK_ADMIN_USER}:${SPLUNK_ADMIN_PASSWORD}"
}

# Is Splunk currently up?
splunk_is_running() {
  sudo -u "${SPLUNK_USER}" "$(splunk_bin)" status 2>/dev/null | grep -qi 'is running'
}

# Copy a directory into place only if it differs, so we can tell whether a
# Splunk restart is actually needed. Sets CHANGED=1 on any change.
# Usage: sync_dir <src> <dst>
sync_dir() {
  local src="$1" dst="$2"
  mkdir -p "$dst"
  if command -v rsync >/dev/null 2>&1; then
    local out
    out="$(rsync -a --itemize-changes --delete "$src"/ "$dst"/)"
    [ -n "$out" ] && CHANGED=1
  else
    # Fallback without rsync: just copy and always flag changed.
    cp -a "$src"/. "$dst"/
    CHANGED=1
  fi
}
