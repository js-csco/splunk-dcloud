#!/usr/bin/env bash
# ===========================================================================
# bootstrap.sh - clone/refresh this repo on the dCloud VM, then run apply.sh.
#
# NOTE: this file lives IN the repo, so it can only run once the repo is on
# disk. The very first fetch needs working DNS + git, which a freshly cloned
# dCloud pod may lack (IP remapping breaks the resolver). That is why the
# canonical dCloud Startup Automation command is self-contained and does the
# DNS fix + clone inline, THEN calls apply.sh directly:
#
#   sudo bash -c 'getent hosts github.com >/dev/null 2>&1 || { rm -f /etc/resolv.conf; printf "nameserver 1.1.1.1\nnameserver 8.8.8.8\n" > /etc/resolv.conf; }; command -v git >/dev/null || { apt-get update -y && apt-get install -y git; }; rm -rf /opt/dcloud-splunk; git clone -b main https://github.com/js-csco/splunk-dcloud.git /opt/dcloud-splunk && exec bash /opt/dcloud-splunk/apply.sh'
#
# This bootstrap.sh remains handy for manually refreshing an existing checkout
# to the latest main and re-applying.
# ===========================================================================
set -euo pipefail

DCLOUD_WORKDIR="${DCLOUD_WORKDIR:-/opt/dcloud-splunk}"
DCLOUD_REPO="${DCLOUD_REPO:-js-csco/splunk-dcloud}"
DCLOUD_BRANCH="${DCLOUD_BRANCH:-main}"
REPO_URL="https://github.com/${DCLOUD_REPO}.git"

echo "== dCloud Splunk lab bootstrap =="
echo "   repo:    ${DCLOUD_REPO}   branch: ${DCLOUD_BRANCH}"
echo "   workdir: ${DCLOUD_WORKDIR}"

run_root() { if [ "$(id -u)" = "0" ]; then "$@"; else sudo "$@"; fi; }

# --- 1) DNS fix (dCloud pod cloning can leave the resolver unreachable) ----
if ! getent hosts github.com >/dev/null 2>&1; then
  echo "DNS cannot resolve github.com - installing static resolver..."
  run_root rm -f /etc/resolv.conf
  printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\noptions timeout:2 attempts:2\n' \
    | run_root tee /etc/resolv.conf >/dev/null
fi

# --- 2) ensure git --------------------------------------------------------
if ! command -v git >/dev/null 2>&1; then
  echo "git not found - installing..."
  export DEBIAN_FRONTEND=noninteractive
  run_root apt-get update -y && run_root apt-get install -y git
fi

# --- 3) clone or refresh --------------------------------------------------
if [ -d "${DCLOUD_WORKDIR}/.git" ]; then
  echo "Refreshing existing checkout..."
  git -C "${DCLOUD_WORKDIR}" fetch --depth 1 origin "${DCLOUD_BRANCH}"
  git -C "${DCLOUD_WORKDIR}" checkout -B "${DCLOUD_BRANCH}" "origin/${DCLOUD_BRANCH}"
  git -C "${DCLOUD_WORKDIR}" reset --hard "origin/${DCLOUD_BRANCH}"
else
  echo "Cloning ${REPO_URL}..."
  run_root rm -rf "${DCLOUD_WORKDIR}"
  git clone --depth 1 --branch "${DCLOUD_BRANCH}" "${REPO_URL}" "${DCLOUD_WORKDIR}"
fi

# --- 4) hand off to the orchestrator --------------------------------------
cd "${DCLOUD_WORKDIR}"
chmod +x apply.sh lib/*.sh 2>/dev/null || true
echo "Handing off to apply.sh..."
exec ./apply.sh
