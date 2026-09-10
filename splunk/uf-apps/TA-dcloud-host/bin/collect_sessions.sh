#!/usr/bin/env bash
# ===========================================================================
# collect_sessions.sh - emit the currently logged-on users (one event each)
# plus a summary count. Scripted input; sourcetype=linux:sessions -> *_linux.
# Pure bash so it runs on a Universal Forwarder (no bundled Python).
# Fields (auto-extracted as key=value): user, tty, from, login_time, event.
# ===========================================================================
set -u
ts="$(date -u +%FT%TZ)"
count=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  user="$(printf '%s' "$line" | awk '{print $1}')"
  tty="$(printf '%s'  "$line" | awk '{print $2}')"
  login="$(printf '%s' "$line" | awk '{print $3" "$4}')"
  from="$(printf '%s' "$line" | sed -n 's/.*(\(.*\)).*/\1/p')"
  [ -z "$from" ] && from="local"
  printf 'event=session status=logged_in user=%s tty=%s from=%s login_time="%s" metric_ts=%s\n' \
    "$user" "$tty" "$from" "$login" "$ts"
  count=$((count + 1))
done < <(who 2>/dev/null)
printf 'event=session_summary logged_in_users=%s metric_ts=%s\n' "$count" "$ts"
