#!/usr/bin/env bash
set -Eeuo pipefail

# Remote-safe bootstrap. When this file is piped through wget/curl, the
# repository is cloned first so the full installer can find its sibling files.
APP_REPO="${REPO_URL:-https://github.com/XISIRUS-SH/CLIENT-VK-TROP.git}"
SCRIPT_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
fi

if [[ -n "${SCRIPT_DIR}" && -f "${SCRIPT_DIR}/scripts/install.sh" ]]; then
  exec bash "${SCRIPT_DIR}/scripts/install.sh" "$@"
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: wget -qO- <install-url> | sudo bash" >&2
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  echo "git is required for remote installation." >&2
  exit 1
fi

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TEMP_DIR}"' EXIT
git clone --depth=1 "${APP_REPO}" "${TEMP_DIR}/repo"
exec env REPO_URL="${APP_REPO}" bash "${TEMP_DIR}/repo/scripts/install.sh" "$@"