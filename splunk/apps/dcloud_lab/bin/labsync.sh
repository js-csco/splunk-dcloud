#!/usr/bin/env bash
# ===========================================================================
# labsync.sh - snapshot the running lab apps to a NEW GitHub branch.
#
#   labsync.sh <branch-name> [commit message]
#
# Creates <branch-name> off main, copies the running lab apps (default + local +
# appserver + metadata) into it, commits and pushes. The repo owner then reviews
# and merges the branch into main; the next session pulls main as usual.
#
# Run by the git-push watcher scripted input (splunkd context = network works),
# and standalone for testing:
#   sudo -u splunk env SPLUNK_HOME=/opt/splunk bash \
#     /opt/splunk/etc/apps/dcloud_lab/bin/labsync.sh added-dashboard "added a dashboard"
#
# Always prints exactly one JSON line: {status,message,commit,branch}
# (status = ok | nochange | error). Never uses 'set -e' so it always reports.
# ===========================================================================
set -uo pipefail

SPLUNK_HOME="${SPLUNK_HOME:-/opt/splunk}"
APPS_DIR="${SPLUNK_HOME}/etc/apps"
TOKEN_FILE="${SPLUNK_HOME}/var/lib/dcloud/gh.token"   # outside any app dir - never committed
REPO="js-csco/splunk-dcloud"
BASE="main"

# --- branch name: from arg 1 (slugified); default lab-snapshot --------------
RAW_BRANCH="${1:-lab-snapshot}"
MSG="${2:-Lab snapshot from Splunk UI}"
# slugify: lowercase, spaces/invalid -> '-', collapse repeats, trim
BRANCH="$(printf '%s' "$RAW_BRANCH" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9._-]+/-/g; s/-+/-/g; s/^-+//; s/-+$//')"
[ -n "$BRANCH" ] || BRANCH="lab-snapshot"

emit() { printf '{"status":"%s","message":"%s","commit":"%s","branch":"%s"}\n' "$1" "$2" "${3:-}" "${4:-}"; }

[ -s "$TOKEN_FILE" ] || { emit "error" "No GitHub token configured on this box - see admin setup (GITHUB_TOKEN at startup)" "" "$BRANCH"; exit 0; }
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

# Keep the user's chosen name; if it already exists on the remote, make it unique
# by appending a timestamp (never overwrite someone else's branch).
if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  BRANCH="${BRANCH}-$(date -u +%H%M%S)"
fi
git checkout -q -B "$BRANCH" "origin/${BASE}"

# Copy each running lab app that this repo manages back into the tree
# (captures dashboards/edits saved into the app's local/ during the session).
for repo_app in "$WORK"/splunk/apps/*/; do
  [ -d "$repo_app" ] || continue
  name="$(basename "$repo_app")"
  src="${APPS_DIR}/${name}"
  [ -d "$src" ] || continue
  if command -v rsync >/dev/null 2>&1; then
    rsync -rl --exclude '.git' "$src"/ "$repo_app"/
  else
    cp -a "$src"/. "$repo_app"/
  fi
done

git add -A
if git diff --cached --quiet; then
  emit "nochange" "No local changes to save" "" "$BRANCH"; exit 0
fi
COUNT="$(git diff --cached --name-only | wc -l | tr -d ' ')"
if ! git commit -q -m "${MSG} ($(date -u +%FT%TZ))"; then
  emit "error" "git commit failed" "" "$BRANCH"; exit 0
fi
SHA="$(git rev-parse --short HEAD)"
if git push -q -u origin "$BRANCH" 2>/dev/null; then
  emit "ok" "Saved ${COUNT} file(s) to branch ${BRANCH} - open a PR to merge into main" "$SHA" "$BRANCH"
else
  emit "error" "Push failed - token needs Contents: write on the repo" "$SHA" "$BRANCH"
fi
