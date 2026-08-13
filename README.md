# AI Balancer

AI Balancer — приватная FastAPI-панель для чатов и маршрутизации запросов
между ключами Groq, OpenAI, Anthropic и OpenAI-compatible/local endpoint.

## Что исправлено

- История чатов хранится в `data/chats/`: каждый чат — отдельный JSON-файл.
- В интерфейсе есть создание, выбор и удаление чатов, а после обновления
  страницы открывается последний активный чат.
- Потоковый ответ собирается только из `delta.content`. Служебные строки
  вроде `Ответ через ключ #1` не попадают в историю и не показываются в чате.
- Модель и провайдер выбираются из активных ключей.
- При добавлении ключа провайдер определяется по префиксу:
  `gsk_...`, `sk-...`, `sk-ant-...`, либо по endpoint.
- Модели загружаются через `/models`, а при временной ошибке используются
  безопасные модели по умолчанию для конкретного провайдера.
- Ключи можно приостанавливать, возобновлять, удалять, переименовывать и
  сортировать по приоритету. Лимиты запросов и токенов отображаются полосами.
- Архивы появляются прямо в сообщении чата и сохраняются после перезагрузки.
- Поддерживаются текстовые `[FILE ...]` и base64-бинарные `[BINARY ...]` блоки.

## Официальные страницы API-ключей

Проверяйте текущие бесплатные лимиты и условия у провайдера перед
использованием:

- Groq: https://console.groq.com/keys
- OpenAI: https://platform.openai.com/api-keys
- Anthropic: https://console.anthropic.com/settings/keys
- Ollama для локального endpoint: https://ollama.com/download

Никогда не публикуйте API-ключи в GitHub, логах, скриншотах или сообщениях.
Ключи шифруются перед записью в SQLite.

## Установка из GitHub

В проекте есть корневой `install.sh`, поэтому после публикации репозитория
работает именно такая команда:

```bash
wget -qO- https://raw.githubusercontent.com/XISIRUS-SH/CLIENT-VK-TROP/main/install.sh | sudo bash
```

Альтернативный вариант:

```bash
curl -fsSL https://raw.githubusercontent.com/XISIRUS-SH/CLIENT-VK-TROP/main/install.sh | sudo bash
```

Установка другого репозитория:

```bash
sudo REPO_URL=https://github.com/your-org/ai-balancer.git \
  bash -c 'wget -qO- https://raw.githubusercontent.com/your-org/ai-balancer/main/install.sh | bash'
```

Установщик:

1. устанавливает Python, SQLite, Git, OpenSSL и системные зависимости;
2. создаёт пользователя `ai-balancer`;
3. клонирует или обновляет проект;
4. создаёт виртуальное окружение и устанавливает `requirements.txt`;
5. сохраняет `.env`, SQLite, историю чатов и архивы при повторном запуске;
6. создаёт self-signed HTTPS-сертификат;
7. регистрирует systemd-сервис и правило UFW;
8. устанавливает команду `wgrt`.

По умолчанию панель доступна по адресу:

```text
https://138.124.103.142:8443/
```

Параметры установки:

```bash
sudo PUBLIC_IP=203.0.113.10 APP_PORT=9443 bash install.sh
```

Для production замените:

```text
/opt/ai-balancer/certs/server.crt
/opt/ai-balancer/certs/server.key
```

На доверенный сертификат и перезапустите сервис:

```bash
sudo systemctl restart ai-balancer
```

## Установка из ZIP

После распаковки архива запускайте установщик из корня проекта:

```bash
cd ai-balancer
sudo bash install.sh
```

Также напрямую доступен основной скрипт:

```bash
sudo bash scripts/install.sh
```

## Команда `wgrt`

После установки:

```bash
sudo wgrt https://github.com/your-org/ai-balancer
```

Команда скачивает корневой `install.sh` указанного репозитория. Если
репозиторий используется в локальной копии:

```bash
sudo bash scripts/wgrt https://github.com/your-org/ai-balancer
```

## Почему появлялась ошибка `${PWD}: unbound variable`

Ошибка:

```text
shell-init: error retrieving current directory: getcwd: cannot access parent directories
bash: PWD: unbound variable
```

появляется, когда shell запущен из каталога, который уже удалили, а скрипт
содержит одновременно `set -u` и обращение к `${PWD}`. В исправленной версии
установщика `${PWD}` не используется для определения исходников: при удалённой
установке репозиторий сначала клонируется во временный каталог.

Если старый скрипт уже скачан и всё равно падает, выполните:

```bash
cd /
wget -qO- https://raw.githubusercontent.com/XISIRUS-SH/CLIENT-VK-TROP/main/install.sh | sudo bash
```

## Работа сервиса

```bash
sudo systemctl status ai-balancer
sudo journalctl -u ai-balancer -f
sudo systemctl restart ai-balancer
```

Логи приложения:

```bash
sudo journalctl -u ai-balancer --no-pager -n 100
```

## Конфигурация

Скопируйте `.env.example` в `.env` и задайте:

- `MASTER_KEY` — Fernet-ключ для шифрования API-ключей;
- `SESSION_SECRET` — длинный секрет сессий;
- `ADMIN_PASSWORD_HASH` — scrypt-хэш пароля администратора;
- `DATA_DIR` — папка для SQLite, чатов и архивов;
- `DATABASE_PATH` — необязательный отдельный путь к SQLite;
- `UPSTREAM_PROXY_URL` — необязательный allow-listed GET proxy.

Сгенерировать хэш пароля:

```bash
python3 - <<'PY'
import base64, getpass, hashlib, secrets
password = getpass.getpass("Admin password: ").encode()
salt = secrets.token_bytes(16)
digest = hashlib.scrypt(password, salt=salt, n=2**14, r=8, p=1)
enc = lambda value: base64.urlsafe_b64encode(value).decode("ascii")
print(f"scrypt$16384$8$1${enc(salt)}${enc(digest)}")
PY
```

Локальный запуск:

```bash
uvicorn app.main:app --reload --port 8443
```

## Полное удаление

Из распакованного проекта:

```bash
sudo bash uninstall.sh
```

Или:

```bash
sudo bash scripts/uninstall.sh
```

Удаление останавливает systemd-сервис, удаляет его unit-файл, правило UFW,
`/opt/ai-balancer`, SQLite, чаты, архивы, сертификаты, виртуальное окружение,
`/usr/local/bin/wgrt` и системного пользователя. Общесистемные Python-пакеты
не удаляются.

Удаление из GitHub:

```bash
cd /
wget -qO- https://raw.githubusercontent.com/XISIRUS-SH/CLIENT-VK-TROP/main/uninstall.sh | sudo bash
```

## Безопасность

- храните `.env` с правами `0600`;
- не добавляйте `.env`, `data/`, сертификаты и приватные ключи в Git;
- замените self-signed-сертификат перед публичным использованием;
- ограничьте порт firewall/VPN или доверенным reverse proxy;
- ZIP-builder блокирует traversal-пути, `.env`, `.git` и повреждённые base64-файлы.