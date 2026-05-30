# Переменные окружения и nginx

Runtime-файлы, env-настройки, nginx архитектура, TLS, маршруты и сборка приложений.

[← К README](../README.md)

## Generated runtime files

`scripts/init.py` и `scripts/generate-config.py` создают runtime-файлы в `generated/<env>/`:

```text
generated/<env>/
├── apps.env
├── compose.frontends.yml
├── deploy.env
├── htpasswd
├── htpasswd.credentials
├── manifest.env
├── nginx.conf
├── routes.env
└── stack.env
```

Назначение:

* `deploy.env` - итоговый env-file для Docker Compose
* `routes.env` - финальные маршруты `route|host|service:port`
* `compose.frontends.yml` - frontend services из `config/apps.yml` (`client-app`, `admin`, `backoffice` и т.п.) с единым hardening-профилем, per-service resource limits и TCP healthcheck
* `nginx.conf` - готовый nginx config
* `htpasswd` - Basic Auth users для management routes (`seq`, `aspire` и т.п.), монтируется в nginx как `/etc/nginx/htpasswd`
* `htpasswd.credentials` - одноразово созданные plaintext-credentials для первого входа; файл не перезаписывается, если `htpasswd` уже существует. Для смены пароля без пересоздания окружения используй `./scripts/cli.py tools rotate-htpasswd <env>`
* `stack.env` - runtime-значения стека: `COMPOSE_PROJECT_NAME`, пути storage/certs, host-порты nginx
* `manifest.env` - hashes source/generated файлов для проверки свежести, а также параметры генерации: `GENERATION_DOMAIN`, `GENERATION_APP_KEYS`, `GENERATION_EXTRA_ROUTES`

### Freshness manifest

`generated/` не хранится в git, а source-файлы вроде `env/common.env`, `env/<env>.env`, `config/apps.yml`, `config/routes.yml` и генератор обновляются через git. После `git pull` на сервере возможна ситуация: source уже новый, а `generated/<env>/manifest.env` всё ещё содержит hashes от старой генерации. Preflight пишет `source is stale or modified`.

Для `dev` `stack preflight` автоматически пересобирает stale generated config. Для `prod` автоматическая регенерация отключена по умолчанию:

```bash
./scripts/cli.py stack preflight prod --allow-regenerate
# или
ALLOW_REGENERATE=1 ./scripts/cli.py stack preflight prod
```

Ручная перегенерация:

```bash
python3 scripts/init.py --env dev --domain yuviron.com --no-up
```

---

## Настройка переменных окружения

В репозитории находятся шаблоны:

```text
env/example.env
env/common.env
```

Создать файлы окружений:

```bash
cp env/example.env env/dev.env
cp env/example.env env/prod.env
```

`env/common.env` и `env/<env>.env` объединяются генератором в `generated/<env>/deploy.env`. Именно `deploy.env` используется как `--env-file` для Docker Compose.

Path-значения генерируются относительно `infra/compose.yml`: например `CERT_FILE=../certs/...`, `STORAGE_PATH=../storage/<env>` и `NGINX_BASIC_AUTH_FILE=../generated/<env>/htpasswd`.

### Env schema

Машиночитаемая схема runtime env лежит в `env/schema.json`. Она описывает обязательные ключи, известные опциональные ключи, базовые форматы (`port`, `nginx-rate`, `cidr-list`, `docker-memory`, `positive-number` и т.п.) и запрещает неописанные переменные на уровне схемы.

`scripts/core/env_validation.py` валидирует итоговый `generated/<env>/deploy.env` через `jsonschema`; пропущенные обязательные ключи и неверные форматы считаются ошибками, а неизвестные ключи выводятся как warning.

### Env validation policy

`stack preflight` и `security audit` проверяют уже объединённый `generated/<env>/deploy.env`.

Для `prod` следующие значения считаются ошибкой:

* `MYSQL_ROOT_PASSWORD=root`
* `Swagger__Enabled=true` или другое truthy-значение (`1`, `yes`, `on`)
* `ASPNETCORE_ENVIRONMENT=Development`
* secret-like env keys с короткими значениями: ключи с `TOKEN`, `API_KEY` или `SECRET` должны иметь минимум 32 символа
* при `stack preflight prod --strict` - weak/default secrets в `MYSQL_ROOT_PASSWORD`, `MYSQL_PASSWORD`, `RABBITMQ_DEFAULT_PASS`, `ASPIRE_FRONTEND_BROWSER_TOKEN` и `ASPIRE_OTLP_API_KEY`

Для `dev` dev-значения вроде `MYSQL_ROOT_PASSWORD=root` и `ASPNETCORE_ENVIRONMENT=Development` допустимы, но короткие secret-like значения дают warning. Минимум для non-prod secret-like значений - 16 символов.

Strict weak-secret policy считает небезопасными: пустые значения, общеизвестные дефолты (`admin`, `password`, `root`, `secret`, `test`, `yuviron`), шаблонные значения вроде `strong_password_1234`, prod-секреты с `dev` в значении, короткие production password/pass и низкоэнтропийные значения с менее чем 5 уникальными символами (например `aaaabbbb`).

### Edge-порты

`HTTP_PORT` и `HTTPS_PORT` задают host-порты, на которые Docker публикует nginx:

* `dev` по умолчанию: `8080/8443`
* `prod` по умолчанию: `80/443`

Переопределение в `env/<env>.env`:

```env
HTTP_PORT=8080
HTTPS_PORT=8443
```

После изменения перегенерировать runtime-файлы:

```bash
python3 scripts/init.py --env dev --domain yuviron.com --no-up
```

### Restart policy

Для `dev` - `unless-stopped`: перезапускает контейнеры после restart Docker daemon или reboot VM, если они не были остановлены вручную. Для `prod` - `always`.

Если dev-стек был остановлен через `stack down`, после перезагрузки VM его нужно поднять явно: `./scripts/cli.py stack up dev`.

### Nginx rate limit

```env
NGINX_PUBLIC_RATE_LIMIT=20r/s
NGINX_PUBLIC_RATE_BURST=40
```

`NGINX_PUBLIC_RATE_LIMIT` управляет скоростью зоны `api_general`, `NGINX_PUBLIC_RATE_BURST` - default burst для routes, у которых не задан явный `rate_limit_burst`. Значения по умолчанию живут в `env/common.env`; для override указывать те же ключи в `env/<env>.env`.

Формат валидируется генератором: rate - `20r/s` или `30r/m`, burst - положительное целое.

Для auth endpoints используется отдельная зона `api_auth` с лимитом `10r/m`. Она включается через `has_auth_endpoints: true` в `config/routes.yml`. Upload locations описываются там же через `upload_locations`.

```yaml
routes:
  public-api:
    target: backend:5073
    rate_limit_zone: api_general
    has_auth_endpoints: true
    upload_locations: [media, upload, uploads, files, file]
    upload_client_max_body_size: 50m
    upload_rate_limit_zone: api_upload
    upload_rate_limit_burst: 2
```

### Frontend resource limits

```env
CLIENT_APP_MEM_LIMIT=256m
CLIENT_APP_MEMSWAP_LIMIT=256m
CLIENT_APP_CPUS=0.25

ADMIN_MEM_LIMIT=256m
ADMIN_MEMSWAP_LIMIT=256m
ADMIN_CPUS=0.25

BACKOFFICE_MEM_LIMIT=256m
BACKOFFICE_MEMSWAP_LIMIT=256m
BACKOFFICE_CPUS=0.25
```

Имя сервиса из `config/apps.yml` превращается в prefix: `client-app` -> `CLIENT_APP`, `backoffice` -> `BACKOFFICE`. Для нового frontend service то же правило: `some-app` -> `SOME_APP_MEM_LIMIT`.

### Пример dev-конфигурации

Только env-специфичные значения — общие (`ConnectionStrings__Redis`, `FILE_STORAGE_ROOT` и т.д.) живут в `env/common.env`.

```env
MYSQL_ROOT_PASSWORD=root
MYSQL_DATABASE=yuviron_dev
MYSQL_USER=yuviron
MYSQL_PASSWORD=yuviron

ASPNETCORE_ENVIRONMENT=Development
ConnectionStrings__Default=server=mysql;port=3306;database=yuviron_dev;user=yuviron;password=yuviron;
Swagger__Enabled=true

HTTP_PORT=80
HTTPS_PORT=443

NGINX_PRIVATE_ACCESS_CIDRS=100.81.228.68/32,100.84.86.48/32

SEQ_FIRSTRUN_ADMINUSERNAME=admin
SEQ_FIRSTRUN_ADMINPASSWORDHASH=<hash>

ASPIRE_FRONTEND_BROWSER_TOKEN=<token>
ASPIRE_OTLP_API_KEY=<api-key>

RABBITMQ_DEFAULT_PASS=<password>
JamendoApi__ClientId=<client-id>

Stripe__SecretKey=sk_test_...
Stripe__WebhookSecret=whsec_...
TAILSCALE_FUNNEL_HOST=nf-1.tail668747.ts.net

NGINX_PUBLIC_RATE_LIMIT=60r/m
NGINX_RATE_API_AUTH=30r/m
NGINX_RATE_API_UPLOAD=20r/m
```

### Пример prod-конфигурации

```env
MYSQL_ROOT_PASSWORD=<strong-password>
MYSQL_DATABASE=yuviron_prod
MYSQL_USER=yuviron
MYSQL_PASSWORD=<strong-password>

ASPNETCORE_ENVIRONMENT=Production
ConnectionStrings__Default=server=mysql;port=3306;database=yuviron_prod;user=yuviron;password=<password>;
Swagger__Enabled=false

HTTP_PORT=80
HTTPS_PORT=443

NGINX_PRIVATE_ACCESS_CIDRS=100.81.228.68/32

SEQ_FIRSTRUN_ADMINUSERNAME=admin
SEQ_FIRSTRUN_ADMINPASSWORDHASH=<hash>

ASPIRE_FRONTEND_BROWSER_TOKEN=<token>
ASPIRE_OTLP_API_KEY=<api-key>

RABBITMQ_DEFAULT_PASS=<strong-password>
JamendoApi__ClientId=<client-id>

Stripe__SecretKey=sk_live_...
Stripe__WebhookSecret=whsec_...

NGINX_CONTENT_SECURITY_POLICY=default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; img-src 'self' data: blob: https:; font-src 'self' data:; style-src 'self'; script-src 'self' blob:; connect-src 'self' https: wss:; media-src 'self' data: blob: https:; worker-src 'self' blob:; manifest-src 'self'

NGINX_PUBLIC_RATE_LIMIT=30r/m
NGINX_RATE_API_AUTH=10r/m
NGINX_RATE_API_UPLOAD=5r/m

# Resource limit overrides (только отличия от common.env):
SEQ_MEM_LIMIT=768m
SEQ_MEMSWAP_LIMIT=768m
BACKEND_MEM_LIMIT=1536m
BACKEND_MEMSWAP_LIMIT=1536m
```

---

## Stripe

Ключи Stripe передаются в backend через env-переменные, которые переопределяют значения из `appsettings.*.json`. Переменные должны быть явно объявлены в секции `environment` сервиса `backend` в `infra/compose.yml` — они **не** попадают в контейнер автоматически через `env_file`.

| Переменная | Где взять |
|---|---|
| `Stripe__SecretKey` | Stripe Dashboard → Developers → API keys → Secret key |
| `Stripe__WebhookSecret` | Stripe Dashboard → Developers → Webhooks → Signing secret |

### Тестирование вебхуков в dev

Серверы Stripe не могут достучаться до `dev-api.yuviron.com` (закрытый Tailscale-домен). Стандартное решение — Stripe CLI `listen`, который создаёт WebSocket-тоннель от Stripe до локального endpoint:

```bash
# Установка
curl -s https://packages.stripe.dev/api/security/keypair/stripe-cli-gpg/public \
  | gpg --dearmor | sudo tee /usr/share/keyrings/stripe.gpg > /dev/null
echo "deb [signed-by=/usr/share/keyrings/stripe.gpg] https://packages.stripe.dev/stripe-cli-debian-local stable main" \
  | sudo tee /etc/apt/sources.list.d/stripe.list
sudo apt update && sudo apt install -y stripe

# Авторизация
stripe login

# Добавить dev-api.yuviron.com в /etc/hosts (если не резолвится локально)
echo "127.0.0.1 dev-api.yuviron.com" | sudo tee -a /etc/hosts

# Запуск туннеля (держать открытым в отдельном терминале)
stripe listen \
  --forward-to https://dev-api.yuviron.com/api/webhooks/stripe \
  --skip-verify
# Скопировать whsec_test_... из вывода → Stripe__WebhookSecret в env/dev.env → restart backend

# Тест
stripe trigger checkout.session.completed
```

`stripe listen` выдаёт временный `whsec_test_...` — его нужно прописать в `Stripe__WebhookSecret` для dev-окружения. При каждом новом запуске `stripe listen` секрет одинаковый для одного аккаунта.

### TAILSCALE_FUNNEL_HOST

Опциональная переменная. Если задана, nginx генерирует дополнительный HTTPS server block для Tailscale Funnel hostname (только `/api/webhooks/` → backend). Используется когда нужен постоянный публичный webhook URL без `stripe listen`.

**Предупреждение**: `tailscale serve` перехватывает весь HTTPS на Tailscale-интерфейсе и ломает доступ к кастомным доменам (`dev-api.yuviron.com`) из Tailscale-сети. Для dev-тестирования рекомендуется `stripe listen`.

---

## Архитектура nginx

Вместо отдельных dev/prod nginx-конфигов - набор Jinja2-шаблонов:

```text
scripts/templates/01-global.conf.j2
scripts/templates/01b-rate-limits.conf.j2
scripts/templates/02-ssl-defaults.conf.j2
scripts/templates/03-routes.conf.j2
scripts/templates/proxy-params.conf
```

Шаблоны рендерятся через `scripts/core/render_nginx.py`; итог записывается в `generated/<env>/nginx.conf` и монтируется в nginx-контейнер при старте стека. Jinja2 работает в режиме `StrictUndefined` - ошибка в шаблоне или контексте падает на генерации, а не превращается в битый nginx config.

Маршруты берутся из `config/routes.yml`; компактное runtime-представление пишется в `generated/<env>/routes.env`.

Перед рендерингом генератор валидирует: route name, host, upstream `service:port`, диапазон портов, `client_max_body_size`, `has_auth_endpoints`, rate-limit и upload-настройки, а также публичные значения `NGINX_PUBLIC_RATE_LIMIT` и `NGINX_PUBLIC_RATE_BURST`.

### Security headers

Security headers задаются только на уровне HTTPS `server` blocks: `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `X-XSS-Protection`, `Referrer-Policy`, `Permissions-Policy` и HSTS - все с директивой `always`.

На уровне `http` эти headers намеренно не задаются: nginx не дополняет `add_header` из `http`, если в `server` уже есть свои `add_header`; глобальный блок выглядел бы как защита, но фактически перекрывался бы server-level headers.

`X-XSS-Protection` оставлен для совместимости со scanner checks и выставлен в `0`, чтобы не включать deprecated browser XSS Auditor; основная защита от XSS - CSP.

Default CSP совместима с dev/Next.js и содержит dev-послабления `'unsafe-inline'` / `'unsafe-eval'`. Для `prod` использование дефолтной CSP является **блокирующей ошибкой**: генерация nginx config и `stack preflight prod` упадут. Задать строгую политику обязательно:

```env
# env/prod.env
NGINX_CONTENT_SECURITY_POLICY=default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; img-src 'self' data: blob: https:; font-src 'self' data:; style-src 'self'; script-src 'self' blob:; connect-src 'self' https:; media-src 'self' data: blob: https:; worker-src 'self' blob:; manifest-src 'self'
```

Строгую политику без `'unsafe-inline'`/`'unsafe-eval'` можно взять из константы `STRICT_CONTENT_SECURITY_POLICY` в `scripts/core/nginx_csp.py`. Для dev `NGINX_CONTENT_SECURITY_POLICY` не обязателен.

### TLS политика

TLS policy задаётся на уровне `http` через named profiles в `scripts/core/render_nginx.py`: `TLS_POLICY_INTERMEDIATE`, `TLS_POLICY_MODERN`. Default - `TLS_POLICY_INTERMEDIATE`.

Default policy:

* разрешает только `TLSv1.2` и `TLSv1.3`
* `ssl_ciphers` - современные AEAD suites для TLS 1.2 по intermediate-профилю Mozilla SSL Config Generator
* для TLS 1.2 включён server cipher order (`ssl_prefer_server_ciphers on`)
* TLS sessions кешируются в shared cache на 1 день
* session tickets отключены
* OCSP stapling намеренно не включён по умолчанию для private/self-signed окружений

TLS policy валидируется перед рендерингом: старые протоколы, неизвестные протоколы и явно слабые cipher markers вроде CBC/RC4/DES отклоняются.

### Режим сертификатов (shared / per-route)

`NGINX_CERT_MODE` задаёт привязку сертификатов к маршрутам:

* `shared` - один общий SAN/wildcard certificate для всех route hosts (`certs/<env>-<domain>.pem`)
* `per-route` - каждый host получает отдельную пару через nginx `map $ssl_server_name ...`; файлы: `certs/<env>/<host>.pem` и `certs/<env>/<host>-key.pem`

Default зависит от окружения: `dev` - `shared`, `prod` - `per-route`. Default HTTPS server всегда использует общий `CERT_FILE`/`KEY_FILE`, чтобы healthcheck и bootstrap nginx работали до выпуска per-route Let's Encrypt файлов.

Переопределить: `NGINX_CERT_MODE=shared` или `NGINX_CERT_MODE=per-route` в `env/<env>.env`.

### Management routes и доступ

Management routes (`seq`, `aspire` и т.п.) закрываются двумя слоями: HTTP Basic Auth и IP/CIDR allowlist.

`NGINX_ADMIN_ALLOWLIST` собирается как `127.0.0.1/32,${NGINX_PRIVATE_ACCESS_CIDRS}`. `NGINX_PRIVATE_ACCESS_CIDRS` не должен быть всем Tailscale CGNAT `100.64.0.0/10` - это общий диапазон Tailscale. Значение `NGINX_PRIVATE_ACCESS_CIDRS` не задаётся в `env/common.env` (этот файл хранится в git). Указывать в `env/<env>.env` на конкретные Tailnet device `/32`, subnet-route CIDR или другой VPN:

```env
NGINX_PRIVATE_ACCESS_CIDRS=100.81.228.68/32,10.8.0.0/24
```

### Media CDN

Route `i` задаётся в `config/routes.yml` с `host_strategy: subdomain` и `media_proxy: true`. Для dev генерирует host `dev-i.<domain>`, для prod - `i.<domain>`.

Внешний URL: `https://i.yuviron.com/<hash>` (без папок и расширений). Nginx переписывает запрос во внутренний backend endpoint `/i/<hash>`.

Для этого route nginx включает `proxy_cache media_cache`:

* успешные `200` ответы кэшируются на `365d`, `404` - только на `1m`
* CORS заголовок `Access-Control-Allow-Origin` выставляется динамически через nginx map `$cors_media_origin`: разрешены только домены из активных non-media маршрутов текущего окружения, не wildcard `*`; `OPTIONS` preflight обрабатывается на edge через named location `@cors_preflight` с ответом `204`
* browser cache header `Cache-Control: public, immutable, max-age=31536000` выставляется только для успешных ответов
* upstream cache headers и cookies скрываются/игнорируются на edge

Файлы именуются GUID-ами - замена контента всегда генерирует новый URL, кэш устаревает только при удалении файла. Для ручной инвалидации конкретной записи или полной очистки кэша:

```bash
./scripts/cli.py stack cache-purge dev
./scripts/cli.py stack cache-purge prod --path /abc123guid
```

### Healthcheck

**Backend:** `wget -qO- http://127.0.0.1:5073/health/ready` — ASP.NET Core health endpoint возвращает JSON со статусами `mysql`, `redis`, `rabbitmq`, `masstransit-bus`. Интервал 15s, retries: 10, start_period: 60s. Логи healthcheck failures подавлены на уровне `Logging:LogLevel` и `Serilog:MinimumLevel:Override`, чтобы транзиентные ошибки при старте стека не засоряли Seq и Aspire.

**Media Worker:** `wget -qO- http://127.0.0.1:5074/health/ready` — аналогичный ASP.NET Core endpoint, но не публикуется наружу; видим только изнутри контейнера. Docker healthcheck: интервал 15s, retries: 10, start_period: 60s. До добавления этого healthcheck зависший worker был незаметен, пока задачи из RabbitMQ-очереди не начинали накапливаться.

**Seq:** `wget -q --spider http://127.0.0.1/health` — HTTP-endpoint самого Seq, доступен без аутентификации. Интервал 15s, retries: 20, start_period: 60s. Образ `datalust/seq` содержит `wget`, TCP-check через `bash /dev/tcp` намеренно не используется — `bash` может отсутствовать в будущих версиях образа.

**Nginx:** контейнер локально проверяет `http://127.0.0.1/health` и срок действия default-сертификата. TLS handshake через `openssl s_client` намеренно не используется внутри Docker healthcheck, чтобы startup health не зависел от хрупкого HTTPS probe. `/health` объявлен до `return 444`, поэтому HTTP healthcheck не зависит от внешнего `Host`.

**Aspire Dashboard:** образ `mcr.microsoft.com/dotnet/aspire-dashboard:9.0` — distroless (chiseled), без shell и unix-утилит. Docker healthcheck отключён (`disable: true`): никакой сервис не зависит от Aspire health, а запустить probe без бинарей внутри контейнера невозможно.

**Frontend (Next.js):** HTTP healthcheck через встроенный Node.js: `http.get('http://127.0.0.1:3000/')`, ответ `< 400` — healthy. TCP-check намеренно заменён на HTTP: открытый сокет не гарантирует, что Node.js обрабатывает запросы. Отдельный `/api/health` endpoint во frontend-репозитории не требуется.

Полная HTTPS-проверка по всем route hosts остаётся в `stack smoke` через `curl --resolve ... 127.0.0.1`.

### Таймауты прокси

Для API-маршрутов задаётся три таймаута:

* `proxy_connect_timeout 5s` — установка TCP-соединения с upstream
* `proxy_send_timeout 15s` — ожидание между двумя записями в upstream
* `proxy_read_timeout 3600s` — ожидание между двумя последовательными чтениями от upstream

`proxy_read_timeout` фиксировано в `3600s` для всех маршрутов. Nginx-директива использует `msec_slot`, который вычисляется при загрузке конфига, а не в рантайме — переменные (`$var`) в ней не работают. Для обычных HTTP-запросов таймаут фактически никогда не срабатывает: upstream отвечает за секунды, а таймаут относится к idle-периоду между чтениями, а не ко времени ответа. WebSocket/SignalR-соединения, которые могут молчать часами, получают нужные 3600s без дополнительной логики.

### Worker processes

`worker_processes` берётся из `NGINX_WORKER_PROCESSES`:

* `dev` по умолчанию: `1` - маленькие VM не плодят лишние воркеры
* `prod` по умолчанию: `auto` - edge nginx использует весь доступный параллелизм

Переопределить в `env/<env>.env`: допустимы `auto` или положительное целое число.

---

## Маршрутизация доменов

### Dev

* `https://dev.yuviron.com` -> `client-app`
* `https://dev-backoffice.yuviron.com` -> `backoffice`
* `https://dev-admin.yuviron.com` -> `admin`
* `https://dev-api.yuviron.com` -> `backend`
* `https://dev-seq.yuviron.com` -> `seq`
* `https://dev-aspire.yuviron.com` -> `aspire-dashboard`
* `https://dev-rabbitmq.yuviron.com` -> `rabbitmq` (management UI, Basic Auth + allowlist)
* `https://dev-i.yuviron.com` -> media CDN

При обращении к Ubuntu VM напрямую без внешнего portproxy dev HTTPS доступен на порту `8443`, например `https://dev.yuviron.com:8443`. При portproxy с `listenport=443` внешний URL остаётся без порта.

### Prod

* `https://yuviron.com` -> `client-app`
* `https://backoffice.yuviron.com` -> `backoffice`
* `https://admin.yuviron.com` -> `admin`
* `https://api.yuviron.com` -> `backend`
* `https://i.yuviron.com` -> media CDN

---

## Сборка приложений

### .NET services

Backend, migrator и media-worker собираются через единый multi-stage Dockerfile `infra/docker/dotnet/Dockerfile`. Targets: `backend`, `migrator`, `media-worker`. Версия .NET (`DOTNET_VERSION`), UID/GID runtime-пользователя (`DOTNET_APP_UID`, `DOTNET_APP_GID`) задаются явно в `env/common.env`; общие security-настройки применяются через `x-dotnet-*` anchors в `infra/compose.yml`. Runtime-пакеты для `backend` и `media-worker` перечислены в `infra/docker/dotnet/runtime-packages.txt`; они не передаются через build args.

`migrator` вынесен в compose profile `migrate`. `./scripts/cli.py stack up <env>` запускает его явно перед основным `up`, а `./scripts/cli.py stack migrate <env>` позволяет выполнить тот же шаг вручную.

Внутри `infra/docker/dotnet/migrator.sh` production fingerprint-guard: при `ASPNETCORE_ENVIRONMENT=Production` контейнер падает, пока `ALLOW_PRODUCTION_MIGRATE` не совпадает с `MYSQL_DATABASE`. Простое `=true` не принимается — нужно указать имя БД. После guard - `dotnet ef database update`, затем `dotnet ef migrations list`; команда падает, если после update остаются pending migrations.

### Frontend

Frontend собирается из монорепозитория через единый Dockerfile с параметром `APP_NAME`. `generated/<env>/compose.frontends.yml` использует относительные build paths: `context` указывает на `../src/yuviron-frontend`, `dockerfile` - на общий frontend Dockerfile.

Ручная сборка:

```bash
docker build \
  -f infra/docker/frontend-next/Dockerfile \
  --build-arg APP_NAME=admin \
  ./src/yuviron-frontend
```

Проверить локальную сборку перед деплоем:

```bash
cd /opt/yuviron-server/src/yuviron-frontend
pnpm install
pnpm turbo run build --filter=client-app --filter=backoffice --filter=admin
```

`pnpm-lock.yaml` должен быть синхронизирован с `package.json`.

---

## Связанные документы

* [Архитектура](architecture.md)
* [Сертификаты](certificates.md)
* [Ротация паролей](passwords.md)
* [CLI и команды](commands.md)
