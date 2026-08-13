#!/usr/bin/env bash
set -Eeuo pipefail

# Remove the AI Balancer service, data, user, and firewall rule.
# This script is intentionally idempotent: missing services and firewall rules
# are treated as already removed.
APP_USER="ai-balancer"
APP_DIR="/opt/ai-balancer"
SERVICE_NAME="ai-balancer"
APP_PORT="${APP_PORT:-8443}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root: sudo bash uninstall.sh" >&2
  exit 1
fi

systemctl stop "${SERVICE_NAME}.service" 2>/dev/null || true
systemctl disable "${SERVICE_NAME}.service" 2>/dev/null || true
systemctl reset-failed "${SERVICE_NAME}.service" 2>/dev/null || true
rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
rm -f /usr/local/bin/wgrt
if command -v ufw >/dev/null 2>&1; then
  ufw delete allow "${APP_PORT}/tcp" 2>/dev/null || true
  ufw delete allow "${APP_PORT}/tcp" comment "AI Balancer HTTPS" 2>/dev/null || true
fi
rm -rf "${APP_DIR}"
if id -u "${APP_USER}" >/dev/null 2>&1; then
  userdel --remove "${APP_USER}" 2>/dev/null || userdel "${APP_USER}" || true
fi
echo "AI Balancer has been removed. System Python packages were left intact."