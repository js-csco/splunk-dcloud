#!/usr/bin/env bash
# ===========================================================================
# labsync.sh - snapshot the running dcloud_lab app to GitHub (snapshot branch).
#
# Invoked by the "Save to GitHub" dashboard (via the labsync search command),
# and also runnable standalone for testing:
#
#   sudo -u splunk env SPLUNK_HOME=/opt/splunk bash \
#     /opt/splunk/etc/apps/dcloud_lab/bin/labsync.sh
#
# Always prints exactly one JSON line: {status,message,commit,branch}
# (status = ok | nochange | error). Never uses 'set -e' so it always reports.
# ===========================================================================
set -uo pipefail

SPLUNK_HOME="${SPLUNK_HOME:-/opt/splunk}"
APP_DIR="${SPLUNK_HOME}/etc/apps/dcloud_lab"
TOKEN_FILE="${SPLUNK_HOME}/var/lib/dcloud/gh.token"   # NOTE: outside the app dir - never committed
REPO="js-csco/splunk-dcloud"
BRANCH="lab-snapshot"
BASE="main"

emit() { printf '{"status":"%s","message":"%s","commit":"%s","branch":"%s"}\n' "$1" "$2" "${3:-}" "${4:-}"; }

[ -s "$TOKEN_FILE" ] || { emit "error" "No GitHub token configured on this box - see admin setup" "" "$BRANCH"; exit 0; }
TOKEN="$(cat "$TOKEN_FILE")"
[ -n "$TOKEN" ] || { emit "error" "Token file is empty" "" "$BRANCH"; exit 0; }

command -v git >/dev/null 2>&1 || { emit "error" "git not found" "" "$BRANCH"; exit 0; }

WORK="$(mktemp -d)" || { emit "error" "could not create temp dir" "" "$BRANCH"; exit 0; }
trap 'rm -rf "$WORK"' EXIT
URL="https://x-access-token:${TOKEN}@github.com/${REPO}.git"

if ! git clone --quiet -b "$BASE" "$URL" "$WORK" 2>/dev/null; then
  emit "error" "git clone failed - check the token and network" "" "$BRANCH"; exit 0
fi
cd "$WORK" || { emit "error" "workdir error" "" "$BRANCH"; exit 0; }
git config user.name  "dCloud Lab (Splunk UI)"
git config user.email "lab@dcloud.local"

# Base the snapshot branch on its remote state if it exists, else on main.
if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  git fetch -q origin "$BRANCH" && git checkout -q -B "$BRANCH" "origin/$BRANCH"
else
  git checkout -q -B "$BRANCH"
fi

# Copy the running app (default + local + appserver + metadata) into the repo.
DEST="splunk/apps/dcloud_lab"
mkdir -p "$DEST"
if command -v rsync >/dev/null 2>&1; then
  rsync -rl --delete --exclude '.git' "$APP_DIR"/ "$DEST"/
else
  rm -rf "$DEST"; mkdir -p "$DEST"; cp -a "$APP_DIR"/. "$DEST"/
fi

git add -A
if git diff --cached --quiet; then
  emit "nochange" "No local changes to save" "" "$BRANCH"; exit 0
fi
COUNT="$(git diff --cached --name-only | wc -l | tr -d ' ')"
if ! git commit -q -m "Lab snapshot from Splunk UI ($(date -u +%FT%TZ))"; then
  emit "error" "git commit failed" "" "$BRANCH"; exit 0
fi
SHA="$(git rev-parse --short HEAD)"
if git push -q -u origin "$BRANCH" 2>/dev/null; then
  emit "ok" "Saved ${COUNT} file(s) to ${BRANCH} - open a PR to merge into main" "$SHA" "$BRANCH"
else
  emit "error" "Push failed - token needs Contents: write on the repo" "$SHA" "$BRANCH"
fi
