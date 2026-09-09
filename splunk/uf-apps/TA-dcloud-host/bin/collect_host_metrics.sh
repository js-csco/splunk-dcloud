#!/usr/bin/env bash
# ===========================================================================
# collect_host_metrics.sh - emit one JSON line of host metrics for a Splunk
# METRIC index (sourcetype=linux:metrics).
#
# Called as a Splunk scripted input:  [script://./bin/collect_host_metrics.sh <site>]
# <site> becomes the "site" dimension (london|berlin|loc1). "host" is added
# automatically by Splunk (the forwarder/host name).
#
# Pure bash + /proc + df — no Python, so it runs on a Universal Forwarder
# (which bundles no Python) and on the Splunk indexer alike. Output is one
# key=value line; the indexer extracts the fields at index time and log-to-
# metrics turns the numeric fields into measures (strings site/os -> dimensions).
#
# NOTE: key=value (not JSON) on purpose. A UF that does INDEXED_EXTRACTIONS=json
# forwards pre-cooked events that BYPASS the indexer parsing pipeline, so the
# metric-schema transform never runs and the metric index drops them. Raw
# key=value forwarded to the indexer goes through parsing (index-time kv
# extraction + metric-schema), which works for both UF and local inputs.
# ===========================================================================
set -u
SITE="${1:-unknown}"
ts="$(date -u +%FT%TZ)"

# --- CPU %: two /proc/stat samples 1s apart --------------------------------
read_cpu() { awk '/^cpu /{print $2,$3,$4,$5,$6,$7,$8}' /proc/stat; }
# shellcheck disable=SC2206
c1=($(read_cpu)); sleep 1; c2=($(read_cpu))
# columns: user nice system idle iowait irq softirq
idle1=$(( ${c1[3]:-0} + ${c1[4]:-0} )); idle2=$(( ${c2[3]:-0} + ${c2[4]:-0} ))
tot1=0; for v in "${c1[@]}"; do tot1=$(( tot1 + v )); done
tot2=0; for v in "${c2[@]}"; do tot2=$(( tot2 + v )); done
dt=$(( tot2 - tot1 )); di=$(( idle2 - idle1 )); diow=$(( ${c2[4]:-0} - ${c1[4]:-0} ))
cpu_pct=0;    [ "$dt" -gt 0 ] && cpu_pct=$(awk "BEGIN{printf \"%.1f\",(1-$di/$dt)*100}")
iowait_pct=0; [ "$dt" -gt 0 ] && iowait_pct=$(awk "BEGIN{printf \"%.1f\",($diow/$dt)*100}")

# --- Memory / swap from /proc/meminfo (kB) ---------------------------------
memtotal=$(awk '/^MemTotal:/{print $2}'     /proc/meminfo)
memavail=$(awk '/^MemAvailable:/{print $2}' /proc/meminfo)
swaptotal=$(awk '/^SwapTotal:/{print $2}'   /proc/meminfo)
swapfree=$(awk '/^SwapFree:/{print $2}'     /proc/meminfo)
memtotal=${memtotal:-0}; memavail=${memavail:-0}; swaptotal=${swaptotal:-0}; swapfree=${swapfree:-0}
mem_used_mb=$(awk "BEGIN{printf \"%.0f\",($memtotal-$memavail)/1024}")
mem_total_mb=$(awk "BEGIN{printf \"%.0f\",$memtotal/1024}")
mem_used_pct=0; [ "$memtotal" -gt 0 ] && mem_used_pct=$(awk "BEGIN{printf \"%.1f\",(1-$memavail/$memtotal)*100}")
swap_used_pct=0; [ "$swaptotal" -gt 0 ] && swap_used_pct=$(awk "BEGIN{printf \"%.1f\",(1-$swapfree/$swaptotal)*100}")

# --- Load average + running procs + uptime + root disk ---------------------
read -r l1 l5 l15 _rest < /proc/loadavg
procs=$(awk '/^procs_running/{print $2}' /proc/stat); procs=${procs:-0}
uptime_s=$(awk '{printf "%.0f",$1}' /proc/uptime)
read -r disk_used_pct disk_free_gb < <(df -P -B1 / | awk 'NR==2{u=$3;a=$4;t=u+a; if(t>0) printf "%.1f %.1f", (u/t)*100, a/1073741824; else printf "0 0"}')

printf 'metric_ts=%s site=%s os=linux cpu_pct=%s cpu_iowait_pct=%s mem_used_pct=%s mem_used_mb=%s mem_total_mb=%s swap_used_pct=%s disk_used_pct=%s disk_free_gb=%s load1=%s load5=%s load15=%s procs=%s uptime_s=%s\n' \
  "$ts" "$SITE" "$cpu_pct" "$iowait_pct" "$mem_used_pct" "$mem_used_mb" "$mem_total_mb" "$swap_used_pct" "$disk_used_pct" "$disk_free_gb" "$l1" "$l5" "$l15" "$procs" "$uptime_s"
