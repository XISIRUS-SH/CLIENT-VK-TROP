#!/usr/bin/env bash
set -Eeuo pipefail

# Rotate the administrator password without removing the database or API keys.
APP_USER="ai-balancer"
APP_DIR="/opt/ai-balancer"
ENV_FILE="${APP_DIR}/.env"
VENV_PYTHON="${APP_DIR}/.venv/bin/python"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root: sudo bash reset-admin-password.sh" >&2
  exit 1
fi
if [[ ! -f "${ENV_FILE}" || ! -x "${VENV_PYTHON}" ]]; then
  echo "AI Balancer is not installed at ${APP_DIR}." >&2
  exit 1
fi

read -r -s -p "New administrator password: " NEW_PASSWORD
echo
read -r -s -p "Repeat new administrator password: " CONFIRM_PASSWORD
echo
if [[ -z "${NEW_PASSWORD}" || "${NEW_PASSWORD}" != "${CONFIRM_PASSWORD}" ]]; then
  echo "Passwords are empty or do not match." >&2
  exit 1
fi

NEW_HASH="$("${VENV_PYTHON}" - "${NEW_PASSWORD}" <<'PY'
import base64
import hashlib
import secrets
import sys

password = sys.argv[1].encode("utf-8")
salt = secrets.token_bytes(16)
n, r, p = 2**14, 8, 1
digest = hashlib.scrypt(password, salt=salt, n=n, r=r, p=p)
encode = lambda value: base64.urlsafe_b64encode(value).decode("ascii")
print(f"scrypt${n}${r}${p}${encode(salt)}${encode(digest)}")
PY
)"

"${VENV_PYTHON}" - "${ENV_FILE}" "${NEW_HASH}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
new_hash = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines()
output = []
replaced = False
for line in lines:
    if line.startswith("ADMIN_PASSWORD_HASH="):
        output.append(f"ADMIN_PASSWORD_HASH={new_hash}")
        replaced = True
    else:
        output.append(line)
if not replaced:
    output.append(f"ADMIN_PASSWORD_HASH={new_hash}")
path.write_text("\n".join(output) + "\n", encoding="utf-8")
PY

chown "${APP_USER}:${APP_USER}" "${ENV_FILE}"
chmod 600 "${ENV_FILE}"
systemctl restart ai-balancer.service
if ! systemctl is-active --quiet ai-balancer.service; then
  journalctl -u ai-balancer.service -n 60 --no-pager >&2 || true
  exit 1
fi
echo "Administrator password was changed successfully."