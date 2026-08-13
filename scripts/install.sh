#!/usr/bin/env bash
set -Eeuo pipefail

# One-command installer for Ubuntu 22.04/24.04.
APP_USER="ai-balancer"
APP_DIR="/opt/ai-balancer"
SERVICE_NAME="ai-balancer"
REPO_URL="${REPO_URL:-https://github.com/XISIRUS-SH/CLIENT-VK-TROP.git}"
PUBLIC_IP="${PUBLIC_IP:-138.124.103.142}"
APP_PORT="${APP_PORT:-8443}"
SCRIPT_PATH="${BASH_SOURCE[0]:-}"
LOCAL_SOURCE=""
if [[ -n "${SCRIPT_PATH}" && -f "${SCRIPT_PATH}" ]]; then
  SCRIPT_DIR="$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)"
  candidate_source="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
  if [[ -f "${candidate_source}/requirements.txt" && -d "${candidate_source}/app" ]]; then
    LOCAL_SOURCE="${candidate_source}"
  fi
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root: sudo bash install.sh" >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This installer supports Ubuntu systems with apt-get." >&2
  exit 1
fi

echo "[1/9] Installing system dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-pip python3-venv git ufw sqlite3 openssl ca-certificates curl

echo "[2/9] Creating service user..."
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "/home/${APP_USER}" --shell /usr/sbin/nologin "${APP_USER}"
fi

echo "[3/9] Cloning or updating the application..."
if [[ -n "${LOCAL_SOURCE}" && "${LOCAL_SOURCE}" != "${APP_DIR}" ]]; then
  echo "Using local application source: ${LOCAL_SOURCE}"
  install -d "${APP_DIR}"
  # Preserve generated data, certificates, the virtual environment, and .env.
  rm -rf "${APP_DIR}/app" "${APP_DIR}/scripts"
  rm -f "${APP_DIR}/requirements.txt" "${APP_DIR}/README.md" \
    "${APP_DIR}/LICENSE" "${APP_DIR}/.gitignore" "${APP_DIR}/.env.example"
  cp -a "${LOCAL_SOURCE}/app" "${APP_DIR}/app"
  cp -a "${LOCAL_SOURCE}/scripts" "${APP_DIR}/scripts"
  cp -a "${LOCAL_SOURCE}/requirements.txt" "${APP_DIR}/requirements.txt"
  for project_file in README.md LICENSE .gitignore .env.example; do
    if [[ -f "${LOCAL_SOURCE}/${project_file}" ]]; then
      cp -a "${LOCAL_SOURCE}/${project_file}" "${APP_DIR}/${project_file}"
    fi
  done
elif [[ "${LOCAL_SOURCE}" == "${APP_DIR}" ]]; then
  echo "Using application source in ${APP_DIR}"
elif [[ -d "${APP_DIR}/.git" ]]; then
  # Root may be updating a checkout owned by the service user.
  git -c safe.directory="${APP_DIR}" -C "${APP_DIR}" fetch --all --prune
  git -c safe.directory="${APP_DIR}" -C "${APP_DIR}" reset --hard origin/main 2>/dev/null \
    || git -c safe.directory="${APP_DIR}" -C "${APP_DIR}" pull --ff-only
else
  rm -rf "${APP_DIR}"
  git clone "${REPO_URL}" "${APP_DIR}"
fi
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

echo "[4/9] Creating Python environment..."
python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

echo "[5/9] Generating private configuration..."
ENV_FILE="${APP_DIR}/.env"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
if [[ -f "${ENV_FILE}" ]]; then
  # Read literal values without sourcing the file. Password hashes contain '$'.
  read_env_value() {
    local key="$1"
    local line
    line="$(grep -E "^${key}=" "${ENV_FILE}" | tail -n 1 || true)"
    printf '%s' "${line#*=}"
  }
  MASTER_KEY="$(read_env_value MASTER_KEY)"
  SESSION_SECRET="$(read_env_value SESSION_SECRET)"
  ADMIN_PASSWORD_HASH="$(read_env_value ADMIN_PASSWORD_HASH)"
  DATA_DIR="$(read_env_value DATA_DIR)"
  DATABASE_PATH="$(read_env_value DATABASE_PATH)"
  UPSTREAM_PROXY_URL="$(read_env_value UPSTREAM_PROXY_URL)"
fi

if [[ -n "${ADMIN_PASSWORD:-}" ]]; then
  # An operator can intentionally rotate the password without deleting data:
  # ADMIN_PASSWORD='new-value' bash install.sh
  ADMIN_PASSWORD_HASH=""
fi

if [[ -z "${ADMIN_PASSWORD_HASH:-}" ]] || ! python3 - "${ADMIN_PASSWORD_HASH}" <<'PY'
import base64
import sys

try:
    parts = sys.argv[1].split("$", 5)
    if len(parts) != 6 or parts[0] != "scrypt":
        raise ValueError
    int(parts[1])
    int(parts[2])
    int(parts[3])
    base64.urlsafe_b64decode(parts[4].encode("ascii"))
    base64.urlsafe_b64decode(parts[5].encode("ascii"))
except (IndexError, ValueError, TypeError):
    raise SystemExit(1)
PY
then
  ADMIN_PASSWORD="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(18))
PY
  )"
  ADMIN_PASSWORD_HASH="$(python3 - "${ADMIN_PASSWORD}" <<'PY'
import base64
import hashlib
import secrets
import sys
password = sys.argv[1].encode()
salt = secrets.token_bytes(16)
n, r, p = 2**14, 8, 1
digest = hashlib.scrypt(password, salt=salt, n=n, r=r, p=p)
enc = lambda value: base64.urlsafe_b64encode(value).decode("ascii")
print(f"scrypt${n}${r}${p}${enc(salt)}${enc(digest)}")
PY
  )"
fi
MASTER_KEY="${MASTER_KEY:-$("${APP_DIR}/.venv/bin/python" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')}"
SESSION_SECRET="${SESSION_SECRET:-$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)}"
DATA_DIR="${DATA_DIR:-${APP_DIR}/data}"
DATABASE_PATH="${DATABASE_PATH:-${DATA_DIR}/ai_balancer.sqlite3}"
install -d -m 750 -o "${APP_USER}" -g "${APP_USER}" "${DATA_DIR}/files"
{
  printf 'MASTER_KEY=%s\n' "${MASTER_KEY}"
  printf 'SESSION_SECRET=%s\n' "${SESSION_SECRET}"
  printf 'ADMIN_PASSWORD_HASH=%s\n' "${ADMIN_PASSWORD_HASH}"
  printf 'DATA_DIR=%s\n' "${DATA_DIR}"
  printf 'DATABASE_PATH=%s\n' "${DATABASE_PATH}"
} > "${ENV_FILE}"
if [[ -n "${UPSTREAM_PROXY_URL:-}" ]]; then
  printf 'UPSTREAM_PROXY_URL=%s\n' "${UPSTREAM_PROXY_URL}" >> "${ENV_FILE}"
fi
chown "${APP_USER}:${APP_USER}" "${ENV_FILE}"
chmod 600 "${ENV_FILE}"

echo "[6/8] Creating a local TLS certificate..."
install -d -m 750 -o "${APP_USER}" -g "${APP_USER}" "${APP_DIR}/certs"
if [[ ! -f "${APP_DIR}/certs/server.key" ]]; then
  openssl req -x509 -nodes -newkey rsa:4096 -days 825 \
    -keyout "${APP_DIR}/certs/server.key" \
    -out "${APP_DIR}/certs/server.crt" \
    -subj "/CN=138.124.103.142" \
    -addext "subjectAltName=IP:138.124.103.142"
  chown "${APP_USER}:${APP_USER}" "${APP_DIR}/certs/server.key" "${APP_DIR}/certs/server.crt"
  chmod 600 "${APP_DIR}/certs/server.key"
fi

echo "[7/8] Registering systemd service and firewall rule..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=AI Balancer Groq control panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
Environment=PYTHONPATH=${APP_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT} --ssl-keyfile ${APP_DIR}/certs/server.key --ssl-certfile ${APP_DIR}/certs/server.crt
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${DATA_DIR}

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
ufw allow "${APP_PORT}/tcp" comment "AI Balancer HTTPS"
systemctl enable --now "${SERVICE_NAME}.service"

if ! systemctl is-active --quiet "${SERVICE_NAME}.service"; then
  echo
  echo "ERROR: the AI Balancer service did not start." >&2
  systemctl --no-pager --full status "${SERVICE_NAME}.service" >&2 || true
  echo >&2
  echo "Recent service log:" >&2
  journalctl -u "${SERVICE_NAME}.service" -n 60 --no-pager >&2 || true
  exit 1
fi

READY=0
for attempt in {1..30}; do
  if ! systemctl is-active --quiet "${SERVICE_NAME}.service"; then
    break
  fi
  if curl --silent --insecure --fail --connect-timeout 2 --max-time 3 \
    "https://127.0.0.1:${APP_PORT}/api/health" >/dev/null; then
    READY=1
    break
  fi
  sleep 1
done

if [[ "${READY}" -ne 1 ]]; then
  echo
  echo "ERROR: the service did not become ready on the configured HTTPS port." >&2
  echo "The private readiness check did not pass; the panel is not ready yet." >&2
  echo "Expected public URL: https://${PUBLIC_IP}:${APP_PORT}/" >&2
  echo "Check the listening socket with: ss -ltnp | grep ${APP_PORT}" >&2
  systemctl --no-pager --full status "${SERVICE_NAME}.service" >&2 || true
  journalctl -u "${SERVICE_NAME}.service" -n 60 --no-pager >&2 || true
  exit 1
fi

echo "[8/9] Installing the wgrt helper..."
install -m 0755 "${APP_DIR}/scripts/wgrt" /usr/local/bin/wgrt

echo "[9/9] Installation complete."
echo
echo "Public URL: https://${PUBLIC_IP}:${APP_PORT}/"
echo "Listening on: 0.0.0.0:${APP_PORT} (public IPv4 ${PUBLIC_IP})"
if [[ -n "${ADMIN_PASSWORD}" ]]; then
  echo "Administrator password: ${ADMIN_PASSWORD}"
else
  echo "Administrator password: unchanged (existing .env was preserved)"
fi
echo "The certificate is self-signed. Replace certs/server.crt and certs/server.key with a trusted certificate for production."
echo "Service status: systemctl status ${SERVICE_NAME}"
echo "Reinstall or install another repository with: sudo wgrt https://github.com/owner/repository"