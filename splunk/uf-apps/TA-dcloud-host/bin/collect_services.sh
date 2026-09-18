#!/usr/bin/env bash
# ===========================================================================
# collect_services.sh - emit one event per systemd service unit with its state.
# Scripted input; sourcetype=linux:services -> *_linux. Pure bash (no Python).
# Fields (auto-extracted as key=value): event, unit, load, active, sub, desc,
# metric_ts. Filter running services in SPL with active=active sub=running.
# Works on any systemd host (Ubuntu, Debian/Proxmox, the LXC containers).
# ===========================================================================
set -u
ts="$(date -u +%FT%TZ)"

if command -v systemctl >/dev/null 2>&1; then
  # --plain drops the leading ●/* status glyph; --no-legend drops header/footer.
  systemctl list-units --type=service --all --plain --no-legend --no-pager 2>/dev/null \
  | while read -r unit load active sub desc; do
      [ -z "$unit" ] && continue
      # everything after the 4th column is the human description
      printf 'event=service unit=%s load=%s active=%s sub=%s desc="%s" metric_ts=%s\n' \
        "$unit" "$load" "$active" "$sub" "$desc" "$ts"
    done
elif command -v service >/dev/null 2>&1; then
  # SysV fallback (no systemd): service --status-all marks running with [ + ].
  service --status-all 2>&1 | while read -r sign _ name; do
    st="unknown"; [ "$sign" = "[+]" ] && st="running"; [ "$sign" = "[-]" ] && st="stopped"
    [ -z "$name" ] && continue
    printf 'event=service unit=%s.service load=loaded active=%s sub=%s desc="" metric_ts=%s\n' \
      "$name" "$([ "$st" = running ] && echo active || echo inactive)" "$st" "$ts"
  done
fi

# summary line: how many services are running right now
if command -v systemctl >/dev/null 2>&1; then
  running="$(systemctl list-units --type=service --state=running --plain --no-legend --no-pager 2>/dev/null | grep -c .)"
  printf 'event=service_summary running_services=%s metric_ts=%s\n' "${running:-0}" "$ts"
fi
