#!/usr/bin/env bash
# ===========================================================================
# collect_processes.sh [limit] - emit one event per running process.
# Scripted input; sourcetype=linux:ps -> *_linux. Pure bash (no Python).
# Fields (auto-extracted as key=value): event, pid, ppid, user, pcpu, pmem,
# rss_kb, comm, metric_ts.
#
# Default: ALL processes (a full snapshot each run). Pass a positive integer to
# cap to the top-N by CPU, e.g. `collect_processes.sh 10` for a lightweight feed.
# Dashboards sort by CPU and take their own head, so emitting all is fine.
# ===========================================================================
set -u
LIMIT="${1:-0}"
ts="$(date -u +%FT%TZ)"

emit() {
  # pid ppid user pcpu pmem rss(KB) comm  — sorted by CPU desc, no header row
  ps -eo pid=,ppid=,user=,pcpu=,pmem=,rss=,comm= --sort=-pcpu 2>/dev/null
}

gen() {
  if [ "${LIMIT}" -gt 0 ] 2>/dev/null; then emit | head -n "${LIMIT}"; else emit; fi
}

gen | while read -r pid ppid user pcpu pmem rss comm; do
  [ -z "$pid" ] && continue
  printf 'event=process pid=%s ppid=%s user=%s pcpu=%s pmem=%s rss_kb=%s comm=%s metric_ts=%s\n' \
    "$pid" "$ppid" "$user" "$pcpu" "$pmem" "$rss" "$comm" "$ts"
done
