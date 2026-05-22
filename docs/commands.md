# CLI и команды

Справочник по `scripts/cli.py`, группам команд и повседневным operations-командам.

[← К README](../README.md)

## CLI (scripts/cli.py)

Основной инструмент управления инфраструктурой - Python CLI:

```bash
./scripts/cli.py <command> <subcommand> [options]
```

CLI является единым интерфейсом для работы со стеком (dev/prod), заменяя разрозненные bash-скрипты.

---

## Общая структура

```bash
./scripts/cli.py <group> <action> [env]
```

* `group` - логическая группа (stack, backup, certs, security, tools)
* `action` - операция
* `env` - окружение (`dev`, `prod`)

`doctor` - отдельная top-level команда:

```bash
./scripts/cli.py doctor dev
./scripts/cli.py doctor prod
./scripts/cli.py doctor dev --strict
./scripts/cli.py doctor dev --isolated
```

Проверяет host-level готовность окружения:

* Docker CLI и доступ к daemon
* Docker Compose plugin
* Tailscale daemon и tailnet IP
* Tailscale MagicDNS: если `100.100.100.100` найден в `/etc/resolv.conf` или `/run/systemd/resolve/resolv.conf`, doctor падает с инструкцией отключить `accept-dns` (`tailscale set --accept-dns=false`), потому что MagicDNS перехватывает системный DNS и ломает внешнюю резолюцию в CI/CD
* DNS для hosts из `generated/<env>/routes.env`
* наличие и базовую валидность default сертификата/ключа
* покрытие route hosts: SAN общего сертификата в `shared` режиме или отдельные per-route сертификаты в `per-route` режиме
* свободность `HTTP_PORT`/`HTTPS_PORT` или их занятость ожидаемым `${COMPOSE_PROJECT_NAME}-nginx`
* наличие обязательных env/runtime values
* generated runtime-файлы
* доступность `STORAGE_PATH`/`SEQ_STORAGE_PATH`
* свободное место на диске
* Docker shared network
* валидность compose config
* валидность nginx config через `nginx -t`
* локальный firewall для `53/tcp`, `53/udp`, `80/tcp`, `443/tcp` и фактических HTTP/HTTPS портов

Флаги:

* `--strict` - считает warning'и ошибками; по умолчанию warning не влияет на exit code
* `--isolated` - запускает compose/nginx валидацию во временном изолированном compose project без влияния на основной запущенный стек; полезно при диагностике без прерывания работающего окружения

---

## Stack

### Preflight (обязательная проверка)

```bash
./scripts/cli.py stack preflight dev
./scripts/cli.py stack preflight prod
./scripts/cli.py stack preflight prod --strict
./scripts/cli.py stack preflight dev --isolated
./scripts/cli.py stack preflight dev --dry-run
./scripts/cli.py stack preflight prod --allow-regenerate
```

Проверяет Docker, env-файлы, env safety policy для prod/dev, сеть, права доступа, nginx конфигурацию, compose конфигурацию, свежесть generated-файлов, валидность маршрутов и соответствие upstream services.

Флаги:

* `--strict` - дополнительно проверяет weak/default secrets; для `prod` такие значения считаются `ERROR` и блокируют preflight. Рекомендуется перед каждым prod-деплоем
* `--isolated` - проверяет compose-конфигурацию без изменений в основном запущенном compose project; полезно при параллельном запуске или диагностике
* `--dry-run` - моделирует container-start часть через `docker compose --dry-run`, не запуская контейнеры; подходит для CI/e2e без реального Docker окружения
* `--allow-regenerate` - разрешает автоматическую пересборку stale `generated/prod/` при обнаружении устаревшего manifest; для `dev` пересборка происходит автоматически, для `prod` требуется явный флаг или `ALLOW_REGENERATE=1`

---

### Запуск

```bash
./scripts/cli.py stack up dev
ALLOW_PRODUCTION_MIGRATE=<db-name> ./scripts/cli.py stack up prod
./scripts/cli.py stack up dev --dry-run
./scripts/cli.py stack up dev --skip-migrate
./scripts/cli.py stack up prod --observability
./scripts/cli.py stack up prod --no-rollback
```

Перед основным `up` CLI запускает EF Core migrator как one-off compose run через profile `migrate`, поэтому миграции выполняются при каждом обычном `stack up`.

Флаги:

* `--dry-run` - не запускает контейнеры: для migrator проверяется compose-план profile `migrate`, для основного `up` дополнительно используется `--no-start`, чтобы Compose проверил план создания/build без ожидания health dependencies; подходит для CI/e2e
* `--skip-migrate` - пропускает EF Core migrator перед запуском сервисов; только для аварийных случаев, когда нужно поднять стек без DB migration step
* `--observability` - дополнительно запускает Seq и Aspire Dashboard (compose profile `observability`); по умолчанию эти сервисы не стартуют, чтобы не потреблять ресурсы в prod без необходимости
* `--no-rollback` - отключает автоматический rollback при сбое сборки или запуска; по умолчанию CLI сохраняет снэпшот текущих образов перед `docker compose up --build` и восстанавливает их при неудаче
* `--no-build` - не пересобирает образы: запускает контейнеры на уже существующих `:latest` образах (`docker compose up -d` без `--build`); одновременно пропускает регенерацию Swagger-документов, поскольку frontend-образы не обновляются; используется в CI-rollback-шаге, где образы предыдущей версии уже восстановлены автоматическим image-level rollback

### Миграции БД

```bash
./scripts/cli.py stack migrate dev
ALLOW_PRODUCTION_MIGRATE=<db-name> ./scripts/cli.py stack migrate prod
./scripts/cli.py stack migrate dev --dry-run
```

`stack migrate` явно запускает EF Core migration контейнер через compose profile `migrate`. `--dry-run` проверяет compose-план profile `migrate` без запуска контейнера.

Для prod CLI добавляет Python-уровневый guard до запуска Docker. **Двойная защита:**

* **Интерактивный режим (TTY):** всегда выводится баннер и требуется ввести `yes, migrate production` — независимо от переменной.
* **Неинтерактивный режим (CI/deploy):** guard требует `ALLOW_PRODUCTION_MIGRATE=<имя-БД>` в `env/prod.env`, где значение должно в точности совпадать с `MYSQL_DATABASE`. Простое `=true` не принимается. Без корректного fingerprint команда завершается с ошибкой, не запуская контейнер.

После Python-guard запускается migrator-контейнер. Внутри `infra/docker/dotnet/migrator.sh` аналогичная fingerprint-проверка на уровне shell: при `ASPNETCORE_ENVIRONMENT=Production` контейнер падает, если `ALLOW_PRODUCTION_MIGRATE` не совпадает с `MYSQL_DATABASE`. После guard выполняется `dotnet ef database update`, затем `dotnet ef migrations list`; команда падает, если после update остаются pending migrations.

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
* smoke paths по типу маршрута: для `api` - `/health/ready`, для management routes (`seq`, `aspire` и т.п.) - `/health`, для остальных маршрутов - `/`

Если `HTTP_PORT`/`HTTPS_PORT` нестандартные, smoke предупреждает, что браузерные URL без явного порта требуют `HTTPS_PORT=443` или внешний portproxy/reverse proxy.

---

### Инвалидация media CDN кэша

```bash
./scripts/cli.py stack cache-purge dev
./scripts/cli.py stack cache-purge prod --path /abc123guid
./scripts/cli.py stack cache-purge prod --yes
```

Управляет nginx media cache (`/var/cache/nginx/yuviron_media`) для route `i` (media CDN).

Без `--path` удаляет все кэшированные файлы целиком. В интерактивном режиме запрашивает подтверждение; `--yes` пропускает его для CI/скриптов.

С `--path /abc123guid` вычисляет MD5 cache key (`https://<i-host>/<path>`) и удаляет ровно один файл через `docker exec nginx find ... -name <md5> -delete`. Если запись уже истекла или никогда не кэшировалась, команда выводит warning без ошибки.

Медиафайлы именуются GUID-ами, поэтому замена контента всегда создаёт новый URL - ручная инвалидация нужна только при удалении файла.

---

## Security

### Audit

```bash
./scripts/cli.py security audit dev
./scripts/cli.py security audit prod
./scripts/cli.py security audit prod --strict
```

Проверяет Docker/infra hardening без запуска контейнеров: `read_only`, `cap_drop`, root containers, published ports, env schema/safety policy, дефолтные секреты, наличие `cert/key`, права `storage`, management routes и возможные секреты среди git-tracked файлов. Audit читает итоговую compose-схему целиком: `infra/compose.yml` вместе с `generated/<env>/compose.frontends.yml`.

По умолчанию команда возвращает non-zero только при `ERROR`; `--strict` считает warning'и ошибками.

---

## Backup

### Создание

```bash
./scripts/cli.py backup create
```

### Создание и проверка

```bash
./scripts/cli.py backup create
./scripts/cli.py backup create --skip-restore-test
./scripts/cli.py backup verify
./scripts/cli.py backup verify --full
./scripts/cli.py backup restore-test dev
./scripts/cli.py backup restore-test prod --archive backups/archives/<archive>.tar.gz
```

Флаги:

* `backup create` - создаёт архив и автоматически запускает restore-test MySQL-дампов
* `backup create --skip-restore-test` - создаёт архив без restore-test; только для аварийных ситуаций
* `backup verify` - быстрая проверка целостности последнего архива и Redis AOF/RDB
* `backup verify --full` - полный restore-test: поднимает временный MySQL, импортирует дамп, проверяет таблицы
* `backup restore-test <env>` - restore-test конкретного окружения из последнего архива
* `backup restore-test <env> --archive <path>` - restore-test из указанного архива

Рекомендуемый полный цикл:

```bash
./scripts/cli.py backup create
./scripts/cli.py backup verify --full
```

---

## Certs

```bash
./scripts/cli.py certs generate --provider mkcert --env dev --domain yuviron.com
./scripts/cli.py certs generate --provider letsencrypt --env prod --domain yuviron.com --email ops@yuviron.com
./scripts/cli.py certs renew --env prod --domain yuviron.com
./scripts/cli.py certs reload --env dev
```

Флаги `certs generate`:

* `--provider mkcert` - самоподписанный сертификат через mkcert + локальный Root CA; подходит для dev
* `--provider letsencrypt` - публичный сертификат от Let's Encrypt через certbot `--webroot`; требует публичный порт 80 и правильный DNS
* `--email` - email для Let's Encrypt аккаунта; если не указан, берётся из переменных окружения `LETSENCRYPT_EMAIL`/`CERTBOT_EMAIL` или запрашивается интерактивно
* `--force-renewal` - принудительный перевыпуск Let's Encrypt сертификата даже если он ещё действителен; без флага используется безопасный режим `--keep-until-expiring`
* `--no-reload` - не выполнять `nginx -s reload` после обновления cert-файлов; полезно если reload планируется вручную
* `--skip-public-check` - пропустить self-check ACME-probe с самого сервера перед вызовом certbot; нужно в cron или средах, где сервер не может достучаться до самого себя по публичному IP

Флаги `certs renew`:

* `--force-renewal` - принудительное продление даже если срок ещё не подходит
* `--no-reload` - не перезагружать nginx после продления
* `--skip-public-check` - пропустить self-check ACME-probe

Прочее:

* `certs reload` - выполняет `nginx -t` внутри контейнера, затем `nginx -s reload`; применяет новые cert-файлы без перезапуска контейнера

Подробнее: [certificates.md](certificates.md).

---

## DNS

### CoreDNS Corefile

```bash
./scripts/cli.py dns generate --env dev --domain yuviron.com --ip 100.81.228.68
```

CoreDNS on Windows - optional local DNS endpoint для dev-доменов. Он нужен, если Windows host обслуживает DNS для RadminVPN legacy-схемы или Tailnet Split DNS; сам private access layer для новых подключений предпочтительно строится через Tailscale.

Команда читает `generated/<env>/routes.env` и записывает CoreDNS-конфиг в:

```text
generated/<env>/Corefile
```

`yuviron.com` и все subdomains получают A-ответ на указанный `--ip` через CoreDNS `template`; `AAAA` для этой зоны явно получает `NXDOMAIN`. Fallback recursive DNS защищён `acl`: по умолчанию рекурсия разрешена только для `100.64.0.0/10`, остальные клиенты не могут использовать CoreDNS как публичный resolver. Остальные разрешённые DNS-запросы форвардятся на `1.1.1.1` и `8.8.8.8`, cache TTL - `300` секунд.

Для другой приватной сети можно указать `--acl-net`:

```bash
./scripts/cli.py dns generate --env dev --domain yuviron.com --ip 100.81.228.68 --acl-net 10.8.0.0/24
```

Corefile не редактируется вручную. После изменения routes/apps/domain нужно перегенерировать runtime config и снова выполнить `dns generate`.

---

## Tools

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

### Внешний мониторинг (UptimeRobot)

Создать мониторы в UptimeRobot для всех публичных HTTPS-эндпоинтов (читает `generated/<env>/routes.env`):

```bash
./scripts/cli.py tools setup-monitoring prod
./scripts/cli.py tools setup-monitoring prod --dry-run      # только предпросмотр
./scripts/cli.py tools setup-monitoring prod --api-key <key>  # без env-переменной
```

Предварительно заполнить `env/common.env` (или `env/prod.env`):

```
UPTIMEROBOT_API_KEY=<ключ из настроек UptimeRobot>
```

Команда пропускает management-роуты (seq, aspire, prometheus…) и media CDN — они недоступны публично. Для API-роута мониторится `/health/ready`, для остальных — `/`. UptimeRobot будет слать email на адрес аккаунта при падении эндпоинта.

---

### Внутренний healthcheck-алертинг (cron)

Установить cron-задание — проверяет `docker ps` каждые 5 минут, при unhealthy-контейнере шлёт email:

```bash
./scripts/cli.py tools setup-healthcheck-cron prod
```

Предварительно заполнить env:

```
ALERTS_EMAIL=ops@example.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=alerts@gmail.com
SMTP_PASSWORD=<app-password>
```

Email-тело указывает причину: **deploy error** (если `stack up` запускался менее 15 минут назад) или **runtime crash** (сервис упал сам). Повторный алерт по тому же контейнеру — не раньше чем через 30 минут. При восстановлении cooldown очищается автоматически.

Проверить алертинг вручную (без cron):

```bash
./scripts/cli.py tools healthcheck-alert prod
```

Лог: `logs/<env>/monitoring/healthcheck-alert.log`

Отправить тестовое письмо для проверки SMTP (не проверяет Docker-состояние, сразу шлёт email):

```bash
./scripts/cli.py tools send-test-alert dev
./scripts/cli.py tools send-test-alert prod
```

Тестовое письмо помечается синим значком 🔵 `[TEST]` и описанием "Test alert", чтобы его нельзя было спутать с реальным алертом. Если SMTP не настроен — команда завершается с ошибкой и объясняет какие переменные отсутствуют.

---

### Мониторинг контейнеров

Одноразовый снимок состояния всех запущенных контейнеров, сгруппированных по compose-проекту:

```bash
./scripts/cli.py tools docker-status
```

Живой TUI-дашборд с CPU, памятью и healthcheck-статусом; обновляется каждые 3 секунды:

```bash
./scripts/cli.py tools docker-dashboard
```

Выход из дашборда — `Ctrl+C`. Контейнеры без healthcheck показываются серым цветом; unhealthy — красным, starting — жёлтым, healthy — зелёным. Внутри каждого проекта unhealthy/starting-контейнеры поднимаются наверх.

---

### Ротация паролей

```bash
./scripts/cli.py tools rotate-htpasswd dev
./scripts/cli.py tools rotate-htpasswd prod

./scripts/cli.py tools rotate-aspire-tokens dev
./scripts/cli.py tools rotate-aspire-tokens prod

./scripts/cli.py tools rotation-status dev
./scripts/cli.py tools rotation-status prod
```

`tools rotate-htpasswd` — пересоздаёт `generated/<env>/htpasswd` и `htpasswd.credentials` с новым случайным паролем. nginx перечитывает htpasswd при каждом аутентифицированном запросе — перезапуск контейнера не нужен.

`tools rotate-aspire-tokens` — генерирует новые `ASPIRE_FRONTEND_BROWSER_TOKEN` и `ASPIRE_OTLP_API_KEY`, обновляет `env/<env>.env`, перегенерирует runtime config и перезапускает только `aspire-dashboard`. Полный перезапуск стека не нужен. Ротация фиксируется в `generated/<env>/rotation.json`.

`tools rotation-status` — показывает, когда последний раз ротировались htpasswd и aspire-токены; завершается с exit code 1, если какой-либо секрет не обновлялся более 90 дней или не ротировался никогда. Удобно добавить в cron или monitoring.

Подробнее обо всех паролях (Seq, Aspire, MySQL, RabbitMQ): [passwords.md](passwords.md).

---

### Остальные tools

```bash
./scripts/cli.py tools check-frontend-fast
./scripts/cli.py tools seq-hash
./scripts/cli.py tools setup-cron
./scripts/cli.py tools setup-certs-cron prod
./scripts/cli.py tools setup-logrotate
./scripts/cli.py tools cleanup
./scripts/cli.py tools cleanup --yes
```

`tools setup-certs-cron` — интерактивно устанавливает cron-задание для автоматического продления Let's Encrypt сертификата; считывает домен из `generated/<env>/manifest.env`, спрашивает время запуска и добавляет строку в crontab (с дедупликацией по маркеру). Подробнее: [certificates.md](certificates.md).

`tools setup-logrotate` — устанавливает `/etc/logrotate.d/yuviron` для ротации логов в `logs/*/nginx/` и `logs/*/letsencrypt/`. Ротация ежедневная, хранится 14 сжатых копий, используется `copytruncate`.

`tools cleanup` — широкий legacy cleanup script с Docker/logs/apt/tmp/generated/certs/.tmp. Автоматически удаляет пустые `*.log`-файлы старше 7 дней из `logs/` и `__pycache__` директории. `--yes` автоматически подтверждает деструктивные промпты (аналог `-y`). Для обычной Docker-очистки используй `tools docker-clean`.

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

Ограничить размер build cache по-другому — через максимально допустимый занятый объём или целевой свободный объём на диске:

```bash
./scripts/cli.py tools docker-clean --mode build-cache --max-used-space 20gb
./scripts/cli.py tools docker-clean --mode build-cache --min-free-space 15gb
```

Все три флага (`--reserved-space`, `--max-used-space`, `--min-free-space`) пробрасываются напрямую в `docker builder prune` и работают как пороги pruning-а. Флаги не совместимы с `--mode safe` и `--mode deep`.

Не используй `--volumes` перед backup/restore и не запускай deep-clean во время активной сборки.

---

## Полезные команды

Просмотр контейнеров:

```bash
./scripts/cli.py tools docker-status
./scripts/cli.py tools docker-dashboard   # live TUI, Ctrl+C для выхода
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
ALLOW_PRODUCTION_MIGRATE=<db-name> ./scripts/cli.py stack up prod
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
