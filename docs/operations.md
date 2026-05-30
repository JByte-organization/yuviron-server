# Operations и запуск стека

Ежедневные операции: preflight, запуск/остановка/обновление стека, ручной Docker Compose и проверка контейнеров.

[← К README](../README.md)

## STACK

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
./scripts/cli.py stack preflight prod --strict
./scripts/cli.py stack preflight dev --isolated
./scripts/cli.py stack preflight dev --dry-run
```

Проверяет: Docker, env-файлы, env safety policy для prod/dev, сеть, права доступа, nginx конфигурацию, compose конфигурацию, свежесть generated-файлов, валидность значений маршрутов и соответствие upstream services compose-конфигурации. В `--strict` режиме дополнительно проверяет weak/default secrets; для `prod` такие значения блокируют preflight.

Если `generated/<env>/manifest.env` устарел, `preflight dev` автоматически пересобирает generated config и повторяет проверку. Для `prod` нужно явное разрешение:

```bash
./scripts/cli.py stack preflight prod --allow-regenerate
# или
ALLOW_REGENERATE=1 ./scripts/cli.py stack preflight prod
```

Для CI/e2e-проверки без запуска контейнеров:

```bash
./scripts/cli.py stack preflight dev --dry-run
./scripts/cli.py stack up dev --dry-run
```

`preflight --dry-run` выполняет обычные файловые/env/compose проверки, но моделирует container-start часть через `docker compose --dry-run` и пропускает runtime-проверки, которым нужен реально запущенный контейнер.

---

### Запуск

```bash
./scripts/cli.py stack up dev
ALLOW_PRODUCTION_MIGRATE=<db-name> ./scripts/cli.py stack up prod

# С Seq и Aspire Dashboard (observability profile):
./scripts/cli.py stack up prod --observability
```

Перед основным `up` CLI сначала тянет pre-built образы сервисов (`docker compose pull --ignore-buildable`; ошибка сети не блокирует запуск), затем запускает EF Core migrator как one-off compose run через profile `migrate`. `--skip-migrate` оставлен для аварийных случаев, когда нужно поднять сервисы без DB migration step.

По умолчанию `stack up` сохраняет снэпшот текущих образов и при сбое сборки или запуска автоматически восстанавливает предыдущее состояние (image-level rollback). Чтобы отключить: `--no-rollback`.

`--no-build` запускает контейнеры без пересборки образов и без регенерации Swagger-документов. Используется в CI-rollback-шаге вместе с `--no-rollback --skip-migrate`: к этому моменту image-level rollback уже восстановил образы предыдущей версии, и повторная сборка не нужна.

---

### Остановка

```bash
./scripts/cli.py stack down dev
./scripts/cli.py stack down prod
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
* smoke paths по типу маршрута: для `api` - `/health/ready`, для management routes (`seq`, `aspire` и т.п.) - `/health`, для остальных - `/`

Если `HTTP_PORT`/`HTTPS_PORT` нестандартные, smoke предупреждает, что браузерные URL без явного порта требуют `HTTPS_PORT=443` или внешний portproxy/reverse proxy.

---

### Инвалидация media CDN кэша

```bash
./scripts/cli.py stack cache-purge dev
./scripts/cli.py stack cache-purge prod --path /abc123guid
./scripts/cli.py stack cache-purge prod --yes
```

Подробнее: [commands.md](commands.md#инвалидация-media-cdn-кэша).

---

## Сценарии

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
ALLOW_PRODUCTION_MIGRATE=<db-name> ./scripts/cli.py stack up prod
./scripts/cli.py stack smoke prod
```

Перед `stack up` убедиться, что backup актуален: `./scripts/cli.py backup verify --full`.

---

### Hard reset (dev)

```bash
./scripts/cli.py stack down dev
./scripts/cli.py tools docker-clean --mode safe
./scripts/cli.py stack up dev
```

---

### Тестирование Stripe вебхуков (dev)

`dev-api.yuviron.com` — закрытый Tailscale-домен, серверы Stripe не могут до него достучаться напрямую. Используй `stripe listen` — он создаёт тоннель через CLI и пробрасывает события локально.

**Терминал 1** (держать открытым):
```bash
stripe listen \
  --forward-to https://dev-api.yuviron.com/api/webhooks/stripe \
  --skip-verify
# Скопировать whsec_test_... → Stripe__WebhookSecret в env/dev.env
# Перезапустить backend если секрет изменился
```

**Терминал 2** (тест):
```bash
stripe trigger checkout.session.completed
docker logs yuviron-dev-backend --follow --tail=20
# Ожидать: [INF] Payment success received for User ...
```

Если `dev-api.yuviron.com` не резолвится на самом сервере (DNS lookup error):
```bash
echo "127.0.0.1 dev-api.yuviron.com" | sudo tee -a /etc/hosts
```

---

## Best Practices

* Всегда запускать `preflight` перед `up`
* Проверять env перед запуском prod (`stack preflight prod --strict`, `security audit prod --strict`)
* Делать backup перед обновлениями
* `backup create` запускает restore-test MySQL-дампов; для полного сценария дополнительно использовать `backup verify --full`
* Не запускать CLI от root без необходимости

Seq запускается non-root под `${SEQ_UID:-1000}:${SEQ_GID:-1000}`. Права для bind-mounted `${SEQ_STORAGE_PATH}` готовятся командами `stack up` и `preflight`. Если запускаешь Docker Compose напрямую в обход CLI, подготовь каталог заранее: `sudo chown -R ${SEQ_UID:-1000}:${SEQ_GID:-1000} <SEQ_STORAGE_PATH>`. У живого Seq включён `cap_drop: ALL`; оставлен только `NET_BIND_SERVICE`, чтобы non-root процесс мог слушать порт `80` внутри контейнера.

---

## Ручной запуск через Docker Compose

При необходимости можно запускать окружения вручную.

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

Это изолирует dev и prod, исключает конфликты имён контейнеров/сетей/volumes и позволяет независимо управлять окружениями.

---

## Проверка контейнеров

Общий просмотр:

```bash
docker ps
```

Проверка через compose:

```bash
docker compose \
  --env-file ./generated/dev/deploy.env \
  -f ./infra/compose.yml \
  -f ./generated/dev/compose.frontends.yml \
  -p yuviron-dev \
  ps
```

Ожидаемые контейнеры запущенного стека:

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
```

`seq` и `aspire-dashboard` входят в compose profile `observability` и по умолчанию **не запускаются**. Чтобы поднять их вместе со стеком:

```bash
./scripts/cli.py stack up prod --observability
```

Отдельный `migrator` запускается как one-off команда через compose profile `migrate` перед `stack up` и обычно не отображается в списке активных контейнеров. При необходимости миграции можно запустить явно:

```bash
./scripts/cli.py stack migrate dev
ALLOW_PRODUCTION_MIGRATE=<db-name> ./scripts/cli.py stack migrate prod
```

---

## Связанные документы

* [Первый запуск](getting-started.md)
* [Справочник команд](commands.md)
* [Диагностика](troubleshooting.md)
* [Ротация паролей](passwords.md)
* [Бэкапы](backups.md)