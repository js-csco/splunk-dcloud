#!/usr/bin/env bash
# ===========================================================================
# bootstrap.sh - the ONE command dCloud Startup Automation runs.
#
# It clones (or refreshes) this repo onto the lab VM and hands off to
# apply.sh, which does the actual Splunk configuration.
#
# Recommended dCloud startup command (single line):
#
#   curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/bootstrap.sh | sudo bash
#
# Everything else lives in the repo, so day-to-day changes are just a
# `git push` - the startup command never has to change.
# ===========================================================================
set -euo pipefail

# --- resolve config -------------------------------------------------------
# These can be overridden by exporting them before invoking bootstrap.sh.
DCLOUD_WORKDIR="${DCLOUD_WORKDIR:-/opt/dcloud-splunk}"
DCLOUD_REPO="${DCLOUD_REPO:-js-csco/splunk-dcloud}"
DCLOUD_BRANCH="${DCLOUD_BRANCH:-main}"
REPO_URL="https://github.com/${DCLOUD_REPO}.git"

echo "== dCloud Splunk lab bootstrap =="
echo "   repo:    ${DCLOUD_REPO}"
echo "   branch:  ${DCLOUD_BRANCH}"
echo "   workdir: ${DCLOUD_WORKDIR}"

# --- ensure git -----------------------------------------------------------
if ! command -v git >/dev/null 2>&1; then
  echo "git not found - installing..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y && apt-get install -y git
fi

# --- clone or refresh -----------------------------------------------------
if [ -d "${DCLOUD_WORKDIR}/.git" ]; then
  echo "Refreshing existing checkout..."
  git -C "${DCLOUD_WORKDIR}" fetch --depth 1 origin "${DCLOUD_BRANCH}"
  git -C "${DCLOUD_WORKDIR}" checkout -B "${DCLOUD_BRANCH}" "origin/${DCLOUD_BRANCH}"
  git -C "${DCLOUD_WORKDIR}" reset --hard "origin/${DCLOUD_BRANCH}"
else
  echo "Cloning ${REPO_URL}..."
  rm -rf "${DCLOUD_WORKDIR}"
  git clone --depth 1 --branch "${DCLOUD_BRANCH}" "${REPO_URL}" "${DCLOUD_WORKDIR}"
fi

# --- hand off to the orchestrator ----------------------------------------
cd "${DCLOUD_WORKDIR}"
chmod +x apply.sh lib/*.sh 2>/dev/null || true
echo "Handing off to apply.sh..."
exec ./apply.sh
