# Runtime-конфигурация

Генерация runtime-файлов, env-настройки, edge-порты, resource limits, nginx и маршрутизация.

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

* `deploy.env` — итоговый env-file для Docker Compose
* `routes.env` — финальные маршруты `route|host|service:port`
* `compose.frontends.yml` — frontend services из `config/apps.yml` (`client-app`, `admin`, `backoffice` и т.п.) с единым hardening-профилем, per-service resource limits и TCP healthcheck
* `nginx.conf` — готовый nginx config
* `htpasswd` — Basic Auth users для внутренних management routes (`seq`, `aspire` и т.п.), монтируется в nginx как `/etc/nginx/htpasswd`
* `htpasswd.credentials` — одноразово созданные plaintext-credentials для первого входа; файл не перезаписывается, если `htpasswd` уже существует
* `stack.env` — runtime-значения стека, включая `COMPOSE_PROJECT_NAME`, пути storage/certs и host-порты nginx
* `manifest.env` — hashes source/generated файлов для проверки свежести, а также параметры генерации: `GENERATION_DOMAIN`, `GENERATION_APP_KEYS`, `GENERATION_EXTRA_ROUTES`

### Freshness manifest

`generated/` не хранится в git, а source-файлы вроде `env/common.env`, `env/<env>.env`, `config/apps.yml`, `config/routes.yml` и генератор обновляются через git. Поэтому после `git pull` на сервере возможна ситуация: source уже новый, а локальный `generated/<env>/manifest.env` всё ещё содержит hashes от старой генерации. В этом случае preflight пишет `source is stale or modified`.

Для `dev` `stack preflight` автоматически пересобирает stale generated config, перечитывает manifest и повторяет проверку свежести. Для регенерации используются параметры из `manifest.env`; для старых manifest без этих полей CLI берёт `BASE_DOMAIN` из `stack.env`/`deploy.env` и `FRONTEND_APP_KEYS` из `apps.env`.

Для `prod` автоматическая регенерация отключена по умолчанию. Это сохраняет явный контроль над production runtime-файлами. Если нужно разрешить автоперегенерацию в CI/deploy, используй:

```bash
./scripts/cli.py stack preflight prod --allow-regenerate
```

или:

```bash
ALLOW_REGENERATE=1 ./scripts/cli.py stack preflight prod
```

Ручная перегенерация по-прежнему доступна:

```bash
python3 scripts/init.py --env dev --domain yuviron.com --no-up
```

---

## Архитектура nginx

Вместо отдельных dev/prod nginx-конфигов используется набор шаблонов:

```text
scripts/templates/01-global.conf.j2
scripts/templates/02-ssl-defaults.conf.j2
scripts/templates/03-routes.conf.j2
scripts/templates/proxy-params.conf
```

Шаблоны `scripts/templates/*.j2` рендерятся через Jinja2 с помощью `scripts/core/render_nginx.py`; `scripts/generate-config.py` записывает итог в `generated/<env>/nginx.conf`, который затем монтируется в nginx-контейнер как `/etc/nginx/nginx.conf` при старте стека.

Различия между окружениями задаются через `env/<env>.env`, `env/common.env`, `config/routes.yml`, `config/apps.yml` и сгенерированные файлы в `generated/<env>/`.

Финальные hosts/upstreams nginx берёт из генерации на основе `config/routes.yml`, а компактное runtime-представление пишет в `generated/<env>/routes.env`.
Перед рендерингом генератор nginx валидирует route name, host, upstream, `client_max_body_size`, `has_auth_endpoints`, а также публичные rate-limit значения `NGINX_PUBLIC_RATE_LIMIT` и `NGINX_PUBLIC_RATE_BURST`; Jinja2 работает в режиме `StrictUndefined`, чтобы ошибка в шаблоне или контексте падала на генерации, а не превращалась в битый nginx config.
Security headers задаются только на уровне HTTPS `server` blocks: `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy` и HSTS используют `always`. На уровне `http` эти headers намеренно не задаются, потому что nginx не дополняет `add_header` из `http`, если в `server` уже есть свои `add_header`; такой глобальный блок выглядел бы как защита, но фактически перекрывался бы server-level headers. Deprecated `X-XSS-Protection` намеренно не используется.
Default CSP остаётся совместимой с dev/Next.js и содержит вынесенные отдельно dev-послабления `'unsafe-inline'` / `'unsafe-eval'`. При генерации `prod` nginx config с такой политикой CLI печатает критический warning. Для публичного prod нужно задать строгую политику через `NGINX_CONTENT_SECURITY_POLICY` в `env/prod.env`, например без unsafe sources.
TLS policy задаётся явно на уровне `http` через named profiles в `scripts/core/render_nginx.py`: `TLS_POLICY_INTERMEDIATE`, `TLS_POLICY_MODERN` и `DEFAULT_TLS_POLICY = TLS_POLICY_INTERMEDIATE`. Default policy разрешает только `TLSv1.2` и `TLSv1.3`, а `ssl_ciphers` фиксирует современные AEAD suites для TLS 1.2 по intermediate-профилю Mozilla SSL Config Generator. TLS 1.3 использует набор suites OpenSSL/nginx для TLS 1.3. Для TLS 1.2 включён server cipher order (`ssl_prefer_server_ciphers on`), TLS sessions кешируются в shared cache на 1 день, session tickets отключены. TLS policy валидируется перед рендерингом: старые протоколы, неизвестные протоколы и явно слабые cipher markers вроде CBC/RC4/DES отклоняются. OCSP stapling намеренно не включён по умолчанию для private/self-signed окружений; его стоит добавлять отдельным public profile, когда cert chain и resolver управляются как часть публичного деплоя.
TLS certificate binding задаётся через `NGINX_CERT_MODE`. В `shared` режиме все HTTPS route server-блоки получают общий сертификат `certs/<env>-<domain>.pem`. В `per-route` режиме nginx генерирует `map $ssl_server_name $route_ssl_certificate` и `$route_ssl_certificate_key`, а каждый host указывает на `certs/<env>/<host>.pem` и `certs/<env>/<host>-key.pem`. Default HTTPS server всегда использует общий `CERT_FILE`/`KEY_FILE`, чтобы healthcheck и bootstrap nginx работали до выпуска per-route Let's Encrypt файлов. Значение по умолчанию окружение-зависимое: `dev` — `shared`, `prod` — `per-route`; при необходимости можно переопределить `NGINX_CERT_MODE=shared` или `NGINX_CERT_MODE=per-route` в `env/<env>.env`.
Management routes (`seq`, `aspire` и т.п.) закрываются двумя слоями: HTTP Basic Auth и IP/CIDR allowlist. `NGINX_ADMIN_ALLOWLIST` по умолчанию собирается как `127.0.0.1/32,${NGINX_PRIVATE_ACCESS_CIDRS}`; `NGINX_PRIVATE_ACCESS_CIDRS=100.64.0.0/10` — это дефолт под Tailscale CGNAT, который можно переопределить в `env/<env>.env` для WireGuard или другого VPN, например `NGINX_PRIVATE_ACCESS_CIDRS=10.8.0.0/24`.

В итоговой compose-схеме nginx ждёт готовности `backend` и сгенерированного `client-app` через `depends_on: condition: service_healthy`.
Все Next.js frontend services используют одинаковый runtime-профиль: `user: 10001:10001`, `read_only: true`, `tmpfs: /tmp`, `cap_drop: ALL`, `security_opt: no-new-privileges:true` и TCP healthcheck порта `3000` внутри контейнера. Resource limits задаются отдельно для каждого frontend service: `client-app` использует `CLIENT_APP_*`, `admin` — `ADMIN_*`, `backoffice` — `BACKOFFICE_*`.
Для frontend healthcheck не требуется отдельный `/api/health` endpoint во frontend-репозитории.
Aspire Dashboard использует exec-form healthcheck через `dotnet --list-runtimes`, потому что официальный image не содержит shell/wget/curl.
Сам nginx также имеет Docker healthcheck: контейнер локально проверяет `http://127.0.0.1/health`, срок действия default-сертификата `/etc/nginx/certs/<env>-<domain>.pem` и HTTPS/TLS endpoint на `127.0.0.1:443`.
`/health` объявлен в default HTTP/HTTPS server-блоках до `return 444`, поэтому проверка не зависит от внешнего `Host`, но всё равно ловит проблемы TLS listener и истёкший сертификат.
В nginx template `worker_processes` берётся из `NGINX_WORKER_PROCESSES`. Значение по умолчанию окружение-зависимое: `dev` генерирует `NGINX_WORKER_PROCESSES=1`, чтобы маленькие VM/container-limits не плодили лишние воркеры; `prod` генерирует `NGINX_WORKER_PROCESSES=auto`, чтобы edge nginx использовал доступный параллелизм на многоядерном хосте. При необходимости значение можно переопределить в `env/<env>.env`: допустимы `auto` или положительное целое число.

Такой подход позволяет:

* избежать дублирования nginx-конфигов
* использовать один source of truth
* упростить поддержку dev и prod
* уменьшить риск расхождения логики маршрутизации между окружениями

---

## Настройка переменных окружения

В репозитории находятся шаблоны окружений:

```text
env/example.env
env/common.env
```

Необходимо создать файлы окружений для каждого окружения (и при необходимости включить общие переменные из `env/common.env`):

```bash
cp env/example.env env/dev.env
cp env/example.env env/prod.env
```

После этого откройте файлы и укажите реальные значения переменных.

`env/common.env` и `env/<env>.env` объединяются генератором в `generated/<env>/deploy.env`.
Именно `deploy.env`, а не исходный `env/dev.env` или `env/prod.env`, используется как `--env-file` для Docker Compose.
Path-значения, которые используются Docker Compose для bind mounts, генерируются относительно `infra/compose.yml`: например `CERT_FILE=../certs/...`, `STORAGE_PATH=../storage/<env>` и `NGINX_BASIC_AUTH_FILE=../generated/<env>/htpasswd`. Сам nginx монтирует весь `../certs` каталог как `/etc/nginx/certs`, а CLI-команды умеют резолвить runtime paths обратно в абсолютные для локальных проверок.

### Env schema

Машиночитаемая схема runtime env лежит в `env/schema.json`. Она описывает обязательные ключи, известные опциональные ключи, базовые форматы (`port`, `nginx-rate`, `cidr-list`, `docker-memory`, `positive-number` и т.п.) и запрещает неописанные переменные на уровне схемы.

`scripts/core/env_validation.py` использует эту схему без внешней JSON Schema зависимости: `stack preflight` и `security audit` проверяют итоговый `generated/<env>/deploy.env`. Пропущенные обязательные ключи и неверные форматы считаются ошибками, а неизвестные ключи выводятся как warning, чтобы новая переменная не появилась бесшумно. Тесты дополнительно проверяют, что ключи из `env/common.env`, `env/example.env` и `${...}`-переменные из `infra/compose.yml` объявлены в схеме.

### Env validation policy

`stack preflight` и `security audit` проверяют итоговый `generated/<env>/deploy.env`, то есть уже объединённые common/env-specific значения.

Для `prod` следующие значения считаются ошибкой:

* `MYSQL_ROOT_PASSWORD=root`
* `Swagger__Enabled=true` или другое truthy-значение (`1`, `yes`, `on`)
* `ASPNETCORE_ENVIRONMENT=Development`
* secret-like env keys с короткими значениями: ключи с `TOKEN`, `API_KEY` или `SECRET` должны иметь минимум 32 символа
* при `stack preflight prod --strict` weak/default secrets в `MYSQL_ROOT_PASSWORD`, `MYSQL_PASSWORD`, `RABBITMQ_DEFAULT_PASS`, `ASPIRE_FRONTEND_BROWSER_TOKEN` и `ASPIRE_OTLP_API_KEY`

Для `dev` dev-значения вроде `MYSQL_ROOT_PASSWORD=root`, `Swagger__Enabled=true` и `ASPNETCORE_ENVIRONMENT=Development` допустимы, но короткие secret-like значения дают warning. Минимум для non-prod secret-like значений — 16 символов.

Strict weak-secret policy считает небезопасными пустые значения, общеизвестные дефолты вроде `admin`, `password`, `root`, `secret`, `test`, `yuviron`, шаблонные значения с `strong_password_1234`, prod-секреты с `dev` в значении и короткие prod password/pass значения.

### Edge-порты

`HTTP_PORT` и `HTTPS_PORT` задают host-порты, на которые Docker публикует nginx:

* `dev` по умолчанию использует `8080/8443`
* `prod` по умолчанию использует `80/443`

Если нужно переопределить порты, укажи их в `env/<env>.env` и затем перегенерируй runtime-файлы:

```env
HTTP_PORT=8080
HTTPS_PORT=8443
```

```bash
./scripts/init.py --env dev --domain yuviron.com --no-up
```

### Restart policy

`RESTART_POLICY` генерируется окружением: для `dev` используется `unless-stopped`, для `prod` — `always`. `unless-stopped` перезапускает контейнеры после restart Docker daemon или reboot VM, если до этого они не были остановлены вручную. Если dev-стек был остановлен через `docker compose stop/down`, после перезагрузки VM его нужно поднять явной командой `./scripts/cli.py stack up dev`.

### Nginx rate limit

Публичный rate limit для основного API location настраивается через env:

```env
NGINX_PUBLIC_RATE_LIMIT=20r/s
NGINX_PUBLIC_RATE_BURST=40
```

`NGINX_PUBLIC_RATE_LIMIT` попадает в `limit_req_zone ... rate=...`, а `NGINX_PUBLIC_RATE_BURST` — в `limit_req ... burst=...`.
Значения по умолчанию лежат в `env/common.env`; для dev/prod override можно указать те же ключи в `env/dev.env` или `env/prod.env` и затем перегенерировать runtime-файлы.

Формат валидируется генератором: rate должен выглядеть как `20r/s` или `30r/m`, burst должен быть положительным целым числом.

Для auth endpoint'ов используется отдельная зона `api_auth` с более строгим лимитом `10r/m`. Она включается не по имени route, а через флаг в `config/routes.yml`:

```yaml
routes:
  api:
    target: backend:5073
    has_auth_endpoints: true
```

Если появится ещё один публичный backend route с `/auth`, `/login`, `/token`, `/refresh` и похожими endpoint'ами, укажи для него `has_auth_endpoints: true`, и генератор добавит отдельный nginx location с `api_auth`.

### Frontend resource limits

Frontend services получают resource limits из env-переменных, построенных по имени сервиса из `config/apps.yml`:

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

Например, service `client-app` превращается в prefix `CLIENT_APP`, а service `backoffice` — в `BACKOFFICE`.
Для нового frontend service правило такое же: `some-app` будет использовать `SOME_APP_MEM_LIMIT`, `SOME_APP_MEMSWAP_LIMIT` и `SOME_APP_CPUS`.

Значения по умолчанию лежат в `env/common.env`; окружение может переопределить их в `env/<env>.env`.

### Пример dev-конфигурации

```env
MYSQL_ROOT_PASSWORD=root
MYSQL_DATABASE=yuviron_dev
MYSQL_USER=yuviron
MYSQL_PASSWORD=yuviron

ASPNETCORE_ENVIRONMENT=Development
ConnectionStrings__Default=server=mysql;port=3306;database=yuviron_dev;user=yuviron;password=yuviron;
ConnectionStrings__Redis=redis:6379
Swagger__Enabled=true

FILE_STORAGE_ROOT=/var/yuviron-server/storage

HTTP_PORT=8080
HTTPS_PORT=8443
```

### Пример prod-конфигурации

```env
MYSQL_ROOT_PASSWORD=...
MYSQL_DATABASE=yuviron_prod
MYSQL_USER=yuviron
MYSQL_PASSWORD=...

ASPNETCORE_ENVIRONMENT=Production
ConnectionStrings__Default=server=mysql;port=3306;database=yuviron_prod;user=yuviron;password=...;
ConnectionStrings__Redis=redis:6379
Swagger__Enabled=false

FILE_STORAGE_ROOT=/var/yuviron-server/storage

HTTP_PORT=80
HTTPS_PORT=443
```

Для env-specific секретов и connection strings ориентируйся на ключи из `env/example.env`; общие инфраструктурные knobs, такие как resource limits, nginx rate limit и `SEQ_UID`/`SEQ_GID` для non-root запуска Seq, по умолчанию живут в `env/common.env` и могут быть переопределены в `env/<env>.env`. `stack up` и `preflight` подготавливают `${SEQ_STORAGE_PATH}` на хосте до запуска контейнера; при ручном `docker compose up` каталог нужно создать и выдать права заранее.

---


---

## Маршрутизация доменов

### Dev

* `https://dev.yuviron.com` → `client-app`
* `https://dev-backoffice.yuviron.com` → `backoffice`
* `https://dev-admin.yuviron.com` → `admin`
* `https://dev-api.yuviron.com` → `backend`
* `https://dev-seq.yuviron.com` → `seq`
* `https://dev-aspire.yuviron.com` → `aspire-dashboard`

Если ты обращаешься к Ubuntu VM напрямую без внешнего portproxy/reverse proxy, dev HTTPS будет доступен на порту `8443`, например `https://dev.yuviron.com:8443`.
При portproxy с `listenport=443` внешний URL остаётся без порта.

Дополнительные dev API endpoints:

* `https://dev-api.yuviron.com/swagger/`
* `https://dev-api.yuviron.com/health/`

Если в nginx сохранена строгая маршрутизация, то:

* на frontend-доменах пути `/api/` и `/swagger/` могут быть намеренно закрыты через `404`
* на `dev-api.yuviron.com` могут быть разрешены только backend endpoint'ы, а остальные пути возвращают `404`

### Prod

* `https://yuviron.com` → `client-app`
* `https://backoffice.yuviron.com` → `backoffice`
* `https://admin.yuviron.com` → `admin`
* `https://api.yuviron.com` → `backend`

---

## Сборка приложений

### .NET services

Backend, migrator и media-worker собираются через единый multi-stage Dockerfile `infra/docker/dotnet/Dockerfile`.
В `infra/compose.yml` для них выбираются разные targets: `backend`, `migrator`, `media-worker`.
Версия .NET, UID/GID runtime-пользователя и общие security-настройки задаются через `x-dotnet-*` anchors и build args.
`migrator` остаётся one-shot сервисом с `restart: "no"` и `depends_on: condition: service_completed_successfully`: внутри `infra/docker/dotnet/migrator.sh` он сначала выполняет `dotnet ef database update`, затем запускает `dotnet ef migrations list` и завершает контейнер с ошибкой, если EF сообщает оставшиеся pending migrations.

### Frontend

Frontend собирается из монорепозитория через единый Dockerfile с параметром `APP_NAME`.
`generated/<env>/compose.frontends.yml` использует относительные build paths, чтобы файл не был привязан к абсолютному пути сервера: `context` указывает на `../src/yuviron-frontend`, а `dockerfile` — на общий frontend Dockerfile относительно build context.

Пример ручной сборки:

```bash
docker build \
  -f infra/docker/frontend-next/Dockerfile \
  --build-arg APP_NAME=admin \
  ./src/yuviron-frontend
```

Для окружений используются следующие приложения:

* `client-app`
* `backoffice`
* `admin`

Перед деплоем рекомендуется проверить локальную сборку:

```bash
cd /opt/yuviron-server/src/yuviron-frontend
pnpm install
pnpm turbo run build --filter=client-app --filter=backoffice --filter=admin
```

Важно:

* `pnpm-lock.yaml` должен быть синхронизирован с `package.json`
* перед CI/CD желательно убедиться, что монорепозиторий собирается локально без ошибок

---

## Универсальная сборка frontend-приложений

Frontend Dockerfile поддерживает параметр `APP_NAME` и может собирать разные приложения из монорепозитория через один общий шаблон.

Пример:

```bash
docker build \
  -f infra/docker/frontend-next/Dockerfile \
  --build-arg APP_NAME=admin \
  ./src/yuviron-frontend
```

Это позволяет не создавать отдельный Dockerfile для каждого frontend-приложения.

---
