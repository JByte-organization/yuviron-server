# Первый запуск

От клонирования репозитория до работающего стека - пошагово.

[← К README](../README.md)

## Требования к серверу

Рекомендуемые параметры виртуальной машины:

```text
OS: Ubuntu 22.04 LTS or newer
CPU: 4 cores
RAM: 8 GB
Disk: 40+ GB
Docker: 24+
Docker Compose Plugin
Docker BuildKit/buildx enabled
Architecture: x64
```

Репозиторий размещается в `/opt/yuviron-server`. Self-hosted runner запускается от обычного пользователя, не root.

---

## 1. Клонирование репозитория

```bash
cd /opt
git clone https://github.com/JByte-organization/yuviron-server
cd yuviron-server
```

---

## 2. Python-зависимости

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Фиксированные зависимости: `PyYAML` (YAML-конфигурация), `Jinja2` (nginx шаблоны), `jsonschema` (env validation). Venv изолирует пакеты от системного Python и других проектов.

---

## 3. Установка Docker

```bash
./scripts/cli.py tools docker-install
```

Скрипт устанавливает Docker Engine, Docker Compose Plugin, создаёт группу `docker` и предлагает добавить пользователя. После добавления нужно перелогиниться.

---

## 4. Первый запуск dev

```bash
cp env/example.env env/dev.env
# Отредактируй env/dev.env - заполни реальные значения
```

Затем:

```bash
python3 scripts/init.py --env dev --domain yuviron.com --no-up
./scripts/cli.py stack preflight dev
./scripts/cli.py doctor dev
./scripts/cli.py stack up dev
./scripts/cli.py stack smoke dev
```

После успешного запуска доступны хосты:

```text
https://dev.yuviron.com            -> client-app
https://dev-backoffice.yuviron.com -> backoffice
https://dev-admin.yuviron.com      -> admin
https://dev-api.yuviron.com        -> backend
https://dev-seq.yuviron.com        -> Seq (Basic Auth)
https://dev-aspire.yuviron.com     -> Aspire Dashboard (Basic Auth)
https://dev-rabbitmq.yuviron.com   -> RabbitMQ Management (Basic Auth)
https://dev-i.yuviron.com          -> media CDN
```

Basic Auth credentials после первого запуска лежат в `generated/dev/htpasswd.credentials`.

---

## 5. Первый запуск prod

```bash
cp env/example.env env/prod.env
# Отредактируй env/prod.env - заполни production-значения
```

Затем:

```bash
python3 scripts/init.py --env prod --domain yuviron.com --no-up
./scripts/cli.py stack preflight prod --strict
./scripts/cli.py security audit prod --strict
ALLOW_PRODUCTION_MIGRATE=<db-name> ./scripts/cli.py stack up prod
./scripts/cli.py stack smoke prod
```

Prod-хосты:

```text
https://yuviron.com             -> client-app
https://backoffice.yuviron.com  -> backoffice
https://admin.yuviron.com       -> admin
https://api.yuviron.com         -> backend
https://i.yuviron.com           -> media CDN
```

---

## Production env checklist

Перед первым prod-запуском - минимальный чеклист для `env/prod.env`:

* `ASPNETCORE_ENVIRONMENT=Production`
* `Swagger__Enabled=false`
* `ALLOW_PRODUCTION_MIGRATE=false` по умолчанию; для запуска мигратора установи значение равным `MYSQL_DATABASE` (имя БД), не `true`
* `MYSQL_ROOT_PASSWORD` не равен `root`
* production secrets не короткие и не похожи на dev/template значения

Проверить итоговый `generated/prod/deploy.env` после редактирования:

```bash
python3 scripts/init.py --env prod --domain yuviron.com --no-up
./scripts/cli.py stack preflight prod --strict
./scripts/cli.py security audit prod --strict
grep -E '^(ASPNETCORE_ENVIRONMENT|DOTNET_ENVIRONMENT)=' generated/prod/deploy.env
```

`stack preflight` и `security audit` блокируют `ASPNETCORE_ENVIRONMENT=Development` для prod, но явная проверка полезна после ручного копирования `env/example.env` в `env/prod.env`.

---

## 6. Сетевой доступ

После запуска стека нужно настроить сетевой доступ к dev-среде:

* **Tailscale** - preferred private access layer. Устанавливается на Linux VM: `sudo tailscale up --accept-dns=false --ssh`
* **RadminVPN** - legacy compatibility для существующих рабочих мест
* **CoreDNS on Windows** - optional local DNS endpoint

Генерация Corefile для CoreDNS:

```bash
./scripts/cli.py dns generate --env dev --domain yuviron.com --ip 100.81.228.68
```

Подробнее о сетях и доступе: [networking.md](networking.md).

---

## 7. Сертификаты

Для dev (mkcert, self-signed):

```bash
./scripts/cli.py certs generate --provider mkcert --env dev --domain yuviron.com
```

Для prod (Let's Encrypt):

```bash
./scripts/cli.py certs generate --provider letsencrypt --env prod --domain yuviron.com --email ops@yuviron.com
```

Чтобы браузеры доверяли dev-сертификатам, нужно установить `rootCA.crt` на машины разработчиков. Подробнее: [certificates.md](certificates.md).

---

## Что дальше

* [Частые операции](operations.md) - deploy, restart, migrate, cache-purge
* [Справочник команд](commands.md) - все команды CLI
* [Диагностика](troubleshooting.md) - что делать, если что-то пошло не так
* [Переменные окружения и nginx](env.md) - все env-настройки, nginx, TLS, маршруты
* [Бэкапы](backups.md) - настройка резервного копирования
* [CI/CD](cicd.md) - настройка self-hosted runner и автодеплоя