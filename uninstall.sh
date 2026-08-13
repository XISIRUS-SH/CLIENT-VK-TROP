#!/usr/bin/env bash
set -Eeuo pipefail

# Remote-safe bootstrap for the complete removal script.
APP_REPO="${REPO_URL:-https://github.com/XISIRUS-SH/CLIENT-VK-TROP.git}"
SCRIPT_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
fi

if [[ -n "${SCRIPT_DIR}" && -f "${SCRIPT_DIR}/scripts/uninstall.sh" ]]; then
  exec bash "${SCRIPT_DIR}/scripts/uninstall.sh" "$@"
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: wget -qO- <uninstall-url> | sudo bash" >&2
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  echo "git is required for remote removal." >&2
  exit 1
fi

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TEMP_DIR}"' EXIT
git clone --depth=1 "${APP_REPO}" "${TEMP_DIR}/repo"
exec bash "${TEMP_DIR}/repo/scripts/uninstall.sh" "$@"