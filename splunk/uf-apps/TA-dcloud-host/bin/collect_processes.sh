#!/usr/bin/env bash
# ===========================================================================
# collect_processes.sh - emit the top processes by CPU (one event each).
# Scripted input; sourcetype=linux:ps -> *_linux. Pure bash (no Python).
# Fields (auto-extracted as key=value): pid, user, pcpu, pmem, comm, event.
# ===========================================================================
set -u
ts="$(date -u +%FT%TZ)"
# Top 10 by CPU. `-o pid=` etc. suppress the header row.
ps -eo pid=,user=,pcpu=,pmem=,comm= --sort=-pcpu 2>/dev/null | head -n 10 | while read -r pid user pcpu pmem comm; do
  [ -z "$pid" ] && continue
  printf 'event=process pid=%s user=%s pcpu=%s pmem=%s comm=%s metric_ts=%s\n' \
    "$pid" "$user" "$pcpu" "$pmem" "$comm" "$ts"
done
