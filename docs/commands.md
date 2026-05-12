# CLI и команды

Справочник по `scripts/cli.py`, группам команд и повседневным operations-командам.

[← К README](../README.md)

## 🚀 CLI (scripts/cli.py)

Основной инструмент управления инфраструктурой — Python CLI:

```bash
./scripts/cli.py <command> <subcommand> [options]
```

CLI является единым интерфейсом для работы со стеком (dev/prod), заменяя разрозненные bash-скрипты.

---

## 📦 Общая структура

```bash
./scripts/cli.py <group> <action> [env]
```

* `group` — логическая группа (stack, backup, certs, security, tools)
* `action` — операция
* `env` — окружение (`dev`, `prod`)

`doctor` — отдельная top-level команда:

```bash
./scripts/cli.py doctor dev
./scripts/cli.py doctor prod
./scripts/cli.py doctor dev --strict
```

Проверяет host-level готовность окружения:

* Docker CLI и доступ к daemon
* Docker Compose plugin
* Tailscale daemon и tailnet IP
* DNS для hosts из `generated/<env>/routes.env`
* наличие и базовую валидность сертификата/ключа
* покрытие route hosts в SAN сертификата
* свободность `HTTP_PORT`/`HTTPS_PORT` или их занятость ожидаемым `${COMPOSE_PROJECT_NAME}-nginx`
* наличие обязательных env/runtime values
* generated runtime-файлы
* доступность `STORAGE_PATH`/`SEQ_STORAGE_PATH`
* свободное место на диске
* Docker shared network
* валидность compose config
* валидность nginx config через `nginx -t`
* локальный firewall для `53/tcp`, `53/udp`, `80/tcp`, `443/tcp` и фактических HTTP/HTTPS портов

По умолчанию warning'и не делают exit code non-zero. `--strict` считает warning'и ошибкой.

---

## 🧱 STACK

### Preflight (обязательная проверка)

```bash
./scripts/cli.py stack preflight dev
./scripts/cli.py stack preflight prod
./scripts/cli.py stack preflight dev --isolated
```

Проверяет:

* Docker
* env-файлы
* сеть
* права доступа
* nginx конфигурацию
* compose конфигурацию
* свежесть generated-файлов через `generated/<env>/manifest.env`
* валидность значений маршрутов для nginx: host, upstream `service:port`, диапазон портов и `client_max_body_size`
* соответствие upstream services из `routes.env` сервисам полной compose-конфигурации

---

### Запуск

```bash
./scripts/cli.py stack up dev
./scripts/cli.py stack up prod
```

---

### Остановка

```bash
./scripts/cli.py stack down dev
./scripts/cli.py stack down prod
```

---

### Перезапуск

```bash
./scripts/cli.py stack down dev
./scripts/cli.py stack up dev
```

---

### Smoke

```bash
./scripts/cli.py stack smoke dev
./scripts/cli.py stack smoke prod
```

Проверяет уже запущенный стек:

* валидность итоговой compose-конфигурации
* наличие обязательных сервисов `mysql`, `redis`, `rabbitmq`, `backend`, `nginx`
* health/status core services и readiness backend внутри контейнера
* HTTPS `/health` для каждого host из `generated/<env>/routes.env` через локальный `curl --resolve ... 127.0.0.1`
* smoke paths по типу маршрута: для `api` — `/health/ready`, для остальных маршрутов — `/`

Если `HTTP_PORT`/`HTTPS_PORT` нестандартные, smoke предупреждает, что браузерные URL без явного порта требуют `HTTPS_PORT=443` или внешний portproxy/reverse proxy.

---

## 🔎 SECURITY

### Audit

```bash
./scripts/cli.py security audit dev
./scripts/cli.py security audit prod
./scripts/cli.py security audit prod --strict
```

Проверяет Docker/infra hardening без запуска контейнеров: `read_only`, `cap_drop`, root containers, published ports, дефолтные секреты, наличие `cert/key`, права `storage`, management routes и возможные секреты среди git-tracked файлов. Audit читает итоговую compose-схему целиком: `infra/compose.yml` вместе с `generated/<env>/compose.frontends.yml`.

По умолчанию команда возвращает non-zero только при `ERROR`; `--strict` считает warning'и ошибками.

---

## 💾 BACKUP

### Создание

```bash
./scripts/cli.py backup create
```

### Проверка восстановления

```bash
./scripts/cli.py backup verify
./scripts/cli.py backup verify --full
./scripts/cli.py backup restore-test dev
```

### Полный цикл

```bash
./scripts/cli.py backup create && ./scripts/cli.py backup verify
```

### Restore-test

```bash
./scripts/cli.py backup restore-test dev
./scripts/cli.py backup restore-test prod --archive backups/archives/<archive>.tar.gz
```

Поднимает временный `mysql:8.4`, импортирует `mysql.sql.gz` выбранного окружения, проверяет восстановленные таблицы и удаляет временный контейнер.

---

## 🔐 CERTS

### Генерация

```bash
./scripts/cli.py certs generate --provider mkcert --env dev --domain yuviron.com
./scripts/cli.py certs generate --provider letsencrypt --env prod --domain yuviron.com --email ops@yuviron.com
./scripts/cli.py certs renew --env prod --domain yuviron.com
```

---

## 🌐 DNS

### CoreDNS Corefile

```bash
./scripts/cli.py dns generate --env dev --domain yuviron.com --ip 100.81.228.68
```

Команда читает `generated/<env>/routes.env` и записывает CoreDNS-конфиг в:

```text
generated/<env>/Corefile
```

Каждый route host получает A/hosts-запись на указанный `--ip`. Остальные DNS-запросы форвардятся на `8.8.8.8` и `1.1.1.1`.

---

## 🛠 TOOLS

### Установка Docker

```bash
./scripts/cli.py tools docker-install
```

### Генерация конфигов

```bash
python3 scripts/generate-config.py --env dev --domain yuviron.com --apps admin,backoffice
```

### Инициализация

```bash
python3 scripts/init.py --env dev --domain yuviron.com
```

### Docker disk cleanup

```bash
./scripts/cli.py tools docker-clean --mode report
./scripts/cli.py tools docker-clean --mode build-cache --reserved-space 10gb
```

### Остальные tools

```bash
./scripts/cli.py tools check-frontend-fast
./scripts/cli.py tools seq-hash
./scripts/cli.py tools setup-cron
./scripts/cli.py tools cleanup
```

`tools cleanup` — широкий legacy cleanup script с Docker/logs/apt/tmp/generated/certs/.tmp. Для обычной Docker-очистки используй `tools docker-clean`.

---

### Docker disk cleanup

Проверить, сколько места занимает Docker:

```bash
./scripts/cli.py tools docker-clean --mode report
./scripts/cli.py tools docker-clean --mode report --verbose
```

Безопасная очистка без удаления named volumes и без полного сброса build cache:

```bash
./scripts/cli.py tools docker-clean --mode safe
```

Освободить место именно из build cache, но оставить кеш для быстрых пересборок:

```bash
./scripts/cli.py tools docker-clean --mode build-cache --reserved-space 10gb
```

Для CI/deploy без интерактивного подтверждения:

```bash
./scripts/cli.py tools docker-clean --mode build-cache --reserved-space 10gb --yes
```

Глубокая очистка, после которой пересборки будут медленнее:

```bash
./scripts/cli.py tools docker-clean --mode deep
```

Очистка unused anonymous volumes включается только явно:

```bash
./scripts/cli.py tools docker-clean --mode deep --volumes
```

Не используй `--volumes` перед backup/restore и не запускай deep-clean во время активной сборки.

---

## Полезные команды

Просмотр контейнеров:

```bash
docker ps
```

Просмотр логов сервиса:

```bash
docker logs <container>
```

Проверка compose-конфигурации dev:

```bash
docker compose \
  --env-file ./generated/dev/deploy.env \
  -f ./infra/compose.yml \
  -f ./generated/dev/compose.frontends.yml \
  -p yuviron-dev \
  config
```

Проверка compose-конфигурации prod:

```bash
docker compose \
  --env-file ./generated/prod/deploy.env \
  -f ./infra/compose.yml \
  -f ./generated/prod/compose.frontends.yml \
  -p yuviron-prod \
  config
```

Проверка nginx внутри контейнера:

```bash
docker exec -it <nginx-container> nginx -t
```

Перезапуск dev:

```bash
./scripts/cli.py stack down dev
./scripts/cli.py stack up dev
```

Перезапуск prod:

```bash
./scripts/cli.py stack down prod
./scripts/cli.py stack up prod
```

Ручная остановка dev-стека:

```bash
docker compose \
  --env-file ./generated/dev/deploy.env \
  -f ./infra/compose.yml \
  -f ./generated/dev/compose.frontends.yml \
  -p yuviron-dev \
  down
```

Ручная остановка prod-стека:

```bash
docker compose \
  --env-file ./generated/prod/deploy.env \
  -f ./infra/compose.yml \
  -f ./generated/prod/compose.frontends.yml \
  -p yuviron-prod \
  down
```

---
