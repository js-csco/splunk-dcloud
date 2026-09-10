#!/usr/bin/env bash
# ===========================================================================
# create-te-agent.sh - run a ThousandEyes Enterprise Agent as a Docker container
# on any Docker-capable lab host (an Ubuntu VM, or the Proxmox host as root).
# Installs Docker if missing, applies the ThousandEyes seccomp/apparmor profiles,
# then (re)creates the agent container.
#
#   # token from env (recommended - keeps it out of the repo):
#   TEAGENT_ACCOUNT_TOKEN='xxxxxxxx' curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/thousandeyes/create-te-agent.sh | sudo -E bash
#   # or on the Proxmox host as root (no sudo):
#   TEAGENT_ACCOUNT_TOKEN='xxxxxxxx' curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/thousandeyes/create-te-agent.sh | bash
#   # (if the token isn't set, the script prompts for it)
#
# Overridable: TE_AGENT_NAME (default dcloud-demo), HOST_VOL_AGENT_DIR (default /opt).
# The agent registers to your ThousandEyes account and appears under
# Cloud & Enterprise Agents; you then build tests against it in the TE portal.
# ===========================================================================
set -euo pipefail

AGENT_NAME="${TE_AGENT_NAME:-dcloud-demo}"
VOL_BASE="${HOST_VOL_AGENT_DIR:-/opt}"
TOKEN="${TEAGENT_ACCOUNT_TOKEN:-}"

run_root() { if [ "$(id -u)" = "0" ]; then "$@"; else sudo "$@"; fi; }

# --- account token (never committed - env or prompt) -----------------------
if [ -z "${TOKEN}" ] && [ -r /dev/tty ]; then
  printf 'ThousandEyes account token: ' > /dev/tty
  IFS= read -r TOKEN < /dev/tty || true
fi
[ -n "${TOKEN}" ] || { echo "ERROR: set TEAGENT_ACCOUNT_TOKEN or enter it when prompted." >&2; exit 1; }

echo "== ThousandEyes Enterprise Agent '${AGENT_NAME}' on $(hostname) =="

# --- Docker --------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker (docker.io)..."
  export DEBIAN_FRONTEND=noninteractive
  run_root apt-get update -y
  run_root apt-get install -y docker.io curl
  run_root systemctl enable --now docker
fi

# --- ThousandEyes seccomp + apparmor profiles ----------------------------
work="$(mktemp -d)"; cd "${work}"
echo "Configuring seccomp & apparmor profiles..."
curl -Os https://downloads.thousandeyes.com/bbot/configure_docker.sh
chmod +x configure_docker.sh
run_root ./configure_docker.sh
cd /; rm -rf "${work}"

# --- persistent volumes --------------------------------------------------
AGENT_DIR="${VOL_BASE}/thousandeyes/${AGENT_NAME}"
run_root mkdir -p "${AGENT_DIR}/te-agent" "${AGENT_DIR}/te-browserbot" "${AGENT_DIR}/log"

# --- (re)create the container --------------------------------------------
run_root docker pull thousandeyes/enterprise-agent:latest >/dev/null 2>&1 || true
run_root docker stop "${AGENT_NAME}" >/dev/null 2>&1 || true
run_root docker rm   "${AGENT_NAME}" >/dev/null 2>&1 || true
run_root docker run \
  --hostname="${AGENT_NAME}" \
  --memory=2g \
  --memory-swap=2g \
  --detach=true \
  --tty=true \
  --shm-size=512M \
  -e TEAGENT_ACCOUNT_TOKEN="${TOKEN}" \
  -e TEAGENT_INET=4 \
  -v "${AGENT_DIR}/te-agent":/var/lib/te-agent \
  -v "${AGENT_DIR}/te-browserbot":/var/lib/te-browserbot \
  -v "${AGENT_DIR}/log/":/var/log/agent \
  --cap-add=NET_ADMIN \
  --cap-add=SYS_ADMIN \
  --name "${AGENT_NAME}" \
  --restart=unless-stopped \
  --security-opt apparmor=docker_sandbox \
  --security-opt seccomp=/var/docker/configs/te-seccomp.json \
  thousandeyes/enterprise-agent:latest /sbin/my_init

echo "Done. Agent '${AGENT_NAME}' started."
echo "  logs:   docker logs -f ${AGENT_NAME}"
echo "  status: docker ps --filter name=${AGENT_NAME}"
echo "It should appear in the ThousandEyes portal (Cloud & Enterprise Agents) shortly;"
echo "then build a test against it there."
