# Operations и запуск стека

Сценарии запуска, preflight, запуск dev/prod, ручной Docker Compose и проверка контейнеров.

[← К README](../README.md)

## 🧱 STACK

### Doctor

```bash
./scripts/cli.py doctor dev
./scripts/cli.py doctor prod
./scripts/cli.py doctor dev --strict
```

Проверяет host-level зависимости перед запуском или разбором проблем: Docker, Compose, Tailscale, DNS, cert/key, публичные порты, env/runtime-файлы, storage, диск, shared network, compose/nginx config и локальный firewall для `53/80/443`. Если `HTTP_PORT`/`HTTPS_PORT` уже заняты ожидаемым `${COMPOSE_PROJECT_NAME}-nginx`, это считается нормальным состоянием.

---

### Preflight (обязательная проверка)

```bash
./scripts/cli.py stack preflight dev
./scripts/cli.py stack preflight prod
./scripts/cli.py stack preflight dev --isolated
```

Проверяет:

* Docker
* env-файлы
* env safety policy для prod/dev
* сеть
* права доступа
* nginx конфигурацию
* compose конфигурацию
* свежесть generated-файлов через `generated/<env>/manifest.env`
* валидность значений маршрутов для nginx: host, upstream `service:port`, диапазон портов и `client_max_body_size`
* соответствие upstream services из `routes.env` сервисам полной compose-конфигурации

Если `generated/<env>/manifest.env` устарел после обновления source-файлов, `preflight dev` автоматически пересобирает generated config и повторяет проверку. Для `prod` это поведение включается только явно: `./scripts/cli.py stack preflight prod --allow-regenerate` или `ALLOW_REGENERATE=1`.

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

## 🔍 Сценарии

### Первый запуск (dev)

```bash
cp env/example.env env/dev.env

python3 scripts/init.py --env dev --domain yuviron.com --no-up
./scripts/cli.py stack preflight dev
./scripts/cli.py stack up dev
./scripts/cli.py stack smoke dev
```

---

### Первый запуск (prod)

```bash
cp env/example.env env/prod.env

python3 scripts/init.py --env prod --domain yuviron.com --no-up
./scripts/cli.py stack preflight prod
./scripts/cli.py stack up prod
./scripts/cli.py stack smoke prod
```

---

### Обновление (deploy)

```bash
git pull

./scripts/cli.py stack preflight dev
./scripts/cli.py stack up dev
./scripts/cli.py stack smoke dev
```

---

### Восстановление после сбоя

```bash
./scripts/cli.py stack down prod
./scripts/cli.py backup verify
./scripts/cli.py stack up prod
```

---

### Hard reset

```bash
./scripts/cli.py stack down dev
./scripts/cli.py tools docker-clean --mode safe

./scripts/cli.py stack up dev
```

---

## ⚠️ Best Practices

* Всегда запускать `preflight` перед `up`
* Проверять env перед запуском prod
* Делать backup перед обновлениями
* Использовать `verify`, а не только `create`
* Не запускать CLI от root без необходимости

Seq запускается non-root: одноразовый `seq-init` без сети делает `chown` для bind-mounted `${SEQ_STORAGE_PATH}` под `${SEQ_UID:-1000}:${SEQ_GID:-1000}`, после чего основной `seq` стартует под этим uid/gid.
У живого Seq включён `cap_drop: ALL`; оставлен только `NET_BIND_SERVICE`, чтобы non-root процесс мог слушать порт `80` внутри контейнера.

---

## Preflight-проверка

Перед запуском окружения рекомендуется выполнять preflight-проверку.

### Dev

```bash
./scripts/cli.py stack preflight dev
```

### Prod

```bash
./scripts/cli.py stack preflight prod
```

Скрипт проверяет:

* наличие обязательных файлов и директорий
* доступ к Docker
* наличие env-файла
* безопасные runtime env-значения: prod запрещает `MYSQL_ROOT_PASSWORD=root`, `Swagger__Enabled=true`, `ASPNETCORE_ENVIRONMENT=Development` и короткие token/API key/secret значения
* права на storage
* свободное место на диске
* наличие сети `yuviron_shared`
* валидность compose-конфигурации
* свежесть generated-файлов по `generated/<env>/manifest.env`
* валидность route hosts, upstreams и client body limits перед nginx validation
* наличие route hosts в `generated/<env>/nginx.conf`
* соответствие upstream services из `routes.env` сервисам compose
* синтаксис nginx через `nginx -t`

Для `dev` stale generated-файлы исправляются прямо внутри preflight: CLI не заходит в интерактивный `init.py`, а запускает `generate-config.py` с параметрами из `manifest.env` или уже сгенерированных `stack.env`/`apps.env`. Для `prod` preflight падает на stale config, пока не передан `--allow-regenerate` или `ALLOW_REGENERATE=1`.

Это позволяет обнаружить типовые проблемы **до запуска контейнеров**.

Для проверки без вмешательства в основной compose project:

```bash
./scripts/cli.py stack preflight dev --isolated
```

---

## Запуск инфраструктуры

### Dev

Запуск:

```bash
cd /opt/yuviron-server
./scripts/cli.py stack up dev
```

Остановка:

```bash
./scripts/cli.py stack down dev
```

### Prod

Запуск:

```bash
cd /opt/yuviron-server
./scripts/cli.py stack up prod
```

Остановка:

```bash
./scripts/cli.py stack down prod
```

---

## Ручной запуск через Docker Compose

При необходимости можно запускать окружения вручную через compose.

### Dev

```bash
docker compose \
  --env-file ./generated/dev/deploy.env \
  -f ./infra/compose.yml \
  -f ./generated/dev/compose.frontends.yml \
  -p yuviron-dev \
  up -d
```

### Prod

```bash
docker compose \
  --env-file ./generated/prod/deploy.env \
  -f ./infra/compose.yml \
  -f ./generated/prod/compose.frontends.yml \
  -p yuviron-prod \
  up -d
```

---

## Compose project names

Для развёртывания используются отдельные project names:

* `yuviron-dev`
* `yuviron-prod`

Это позволяет:

* изолировать dev и prod
* исключить конфликты имён контейнеров, сетей и volume
* независимо управлять окружениями

---

## Проверка контейнеров

Общий просмотр контейнеров:

```bash
docker ps
```

Проверка compose-стека dev:

```bash
docker compose \
  --env-file ./generated/dev/deploy.env \
  -f ./infra/compose.yml \
  -f ./generated/dev/compose.frontends.yml \
  -p yuviron-dev \
  ps
```

Проверка compose-стека prod:

```bash
docker compose \
  --env-file ./generated/prod/deploy.env \
  -f ./infra/compose.yml \
  -f ./generated/prod/compose.frontends.yml \
  -p yuviron-prod \
  ps
```

Ожидаемо в активном состоянии могут быть контейнеры примерно такого типа:

```text
nginx
mysql
redis
rabbitmq
backend
media-worker
client-app
backoffice
admin
seq
aspire-dashboard
```

`seq-init` и отдельный `migrator` могут завершаться после успешного применения своей одноразовой работы и не отображаться в списке активных контейнеров.

---
