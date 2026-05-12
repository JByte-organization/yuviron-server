# Архитектура

Рабочая схема инфраструктуры Yuviron: доступ разработчиков, DNS, edge routing, приложения, сервисы данных, observability и runtime generation.

[← К README](../README.md)

## Главное

Инфраструктура строится вокруг одного Docker Compose стека и одного edge nginx. Различия между `dev` и `prod` задаются конфигурацией, env-файлами и сгенерированными runtime-файлами в `generated/<env>/`.

Ключевая идея:

* **Tailscale** — preferred private access layer для dev.
* **RadminVPN** — legacy compatibility для старых рабочих мест.
* **CoreDNS on Windows** — optional local DNS endpoint, а не отдельный access layer.
* **Nginx Edge** — единственная входная точка HTTP/HTTPS в Docker-стек.
* **MySQL / Redis / RabbitMQ** — внутренние сервисы данных, не публикуются наружу.
* **Seq / Aspire** — internal observability, внешние dev routes защищены Basic Auth.

---

## High-level схема

```text
Developer / Browser / Mobile / CI smoke
        |
        | 1. DNS lookup для *.yuviron.com
        v
+-------------------------- DNS layer --------------------------+
| prod / public access: public DNS                              |
| dev preferred: Tailnet DNS / public DNS / explicit Tailnet IP |
| dev legacy: optional CoreDNS on Windows                       |
+---------------------------------------------------------------+
        |
        | 2. HTTPS request к resolved IP
        v
+---------------------- Private/public access ----------------------+
| Tailscale preferred: Developer -> Tailnet -> Linux VM / dev-vm    |
| RadminVPN legacy: Developer -> RadminVPN -> Windows host          |
|                   -> portproxy -> Ubuntu VM                      |
| Prod/public:      Client -> public network -> production server   |
+-------------------------------------------------------------------+
        |
        v
+---------------------------- Linux VM -----------------------------+
| Docker host                                                       |
|                                                                   |
|  :80 / :443, или dev :8080 / :8443 за внешним proxy/portproxy     |
|        |                                                          |
|        v                                                          |
|  +----------------------- Nginx Edge --------------------------+  |
|  | TLS termination, route dispatch, rate limits, Basic Auth     |  |
|  +-------------------------------------------------------------+  |
|        |                                                          |
|        +--> client-app:3000                                      |
|        +--> admin:3000                                           |
|        +--> backoffice:3000                                      |
|        +--> backend:5073                                         |
|        +--> seq:80                  dev management, Basic Auth   |
|        +--> aspire-dashboard:18888  dev management, Basic Auth   |
|                                                                   |
|  backend / media-worker / migrator                                |
|        |                                                          |
|        +--> MySQL                                                 |
|        +--> Redis                                                 |
|        +--> RabbitMQ                                              |
|        +--> shared file storage                                   |
|        +--> Seq logs                                              |
|        +--> Aspire OTLP                                           |
+-------------------------------------------------------------------+
```

---

## DNS и access layer

DNS и access layer не смешиваются:

* DNS отвечает на вопрос: "в какой IP резолвится `dev-api.yuviron.com`?"
* access layer отвечает на вопрос: "по какому сетевому пути клиент дойдёт до этого IP?"

Для dev есть несколько допустимых схем:

```text
Preferred:

Developer
  -> Tailscale
  -> Linux VM / dev-vm
  -> Nginx Edge
```

```text
Legacy compatibility:

Developer
  -> RadminVPN
  -> CoreDNS on Windows для *.yuviron.com
  -> Windows portproxy
  -> Ubuntu VM
  -> Nginx Edge
```

```text
Optional Tailnet Split DNS:

Developer
  -> Tailscale
  -> CoreDNS on Windows как DNS endpoint
  -> Linux VM / dev-vm
  -> Nginx Edge
```

CoreDNS on Windows нужен только если локальный DNS endpoint действительно используется. Он не является обязательным компонентом Tailscale-доступа и не проксирует HTTP/HTTPS трафик.

---

## Edge routing

Source of truth для маршрутов — `config/routes.yml`. Runtime-представление лежит в `generated/<env>/routes.env`, а итоговый nginx config — в `generated/<env>/nginx.conf`.

Типичные dev routes:

```text
dev.yuviron.com            -> client-app:3000
dev-admin.yuviron.com      -> admin:3000
dev-backoffice.yuviron.com -> backoffice:3000
dev-api.yuviron.com        -> backend:5073
dev-seq.yuviron.com        -> seq:80
dev-aspire.yuviron.com     -> aspire-dashboard:18888
```

В nginx:

* `client`, `admin`, `backoffice` идут в frontend containers.
* `api` идёт в backend на `5073`.
* `seq` и `aspire` считаются management routes и получают HTTP Basic Auth.
* `/health` обслуживается самим edge nginx для healthcheck и smoke.
* API routes получают nginx rate limiting.
* TLS certificate/key монтируются в nginx как read-only файлы.

---

## Application layer

```text
Nginx Edge
  |
  +-- client-app       Next.js client frontend
  +-- admin            Next.js admin frontend
  +-- backoffice       Next.js backoffice frontend
  +-- backend          Yuviron API
```

Frontend services генерируются из `config/apps.yml` в `generated/<env>/compose.frontends.yml`. Это позволяет включать/выключать frontend apps конфигурацией, не копируя compose-секции вручную.

Backend использует общие `.NET` build args, env и hardening-профиль из YAML anchors `x-dotnet-*` в `infra/compose.yml`. Такой же профиль используют `migrator` и `media-worker`.

---

## Data и background services

```text
backend
  |
  +-- MySQL       основная БД
  +-- Redis       cache / transient state
  +-- RabbitMQ    messaging
  +-- storage     файлы приложения
  +-- Seq         structured logs
  +-- Aspire      OTLP / dashboard

media-worker
  |
  +-- MySQL / Redis / RabbitMQ
  +-- storage

migrator
  |
  +-- MySQL
```

Сервисы данных находятся во внутренней Docker network и не публикуются как host ports. Внешний HTTP/HTTPS трафик должен входить через nginx.

Persisted state:

* `mysql_data` — MySQL data volume.
* `redis_data` — Redis appendonly data volume.
* `rabbitmq_data` — RabbitMQ data volume.
* `${STORAGE_PATH}` — файловое хранилище приложения.
* `${SEQ_STORAGE_PATH}` — Seq storage.

---

## Observability и management routes

Seq и Aspire нужны для диагностики dev-инфраструктуры:

* **Seq** принимает structured logs.
* **Aspire Dashboard** принимает OTLP telemetry и даёт dashboard для наблюдения.

Внешние dev routes:

```text
dev-seq.yuviron.com
dev-aspire.yuviron.com
```

защищены Basic Auth на nginx edge. Credentials создаются в runtime generation:

```text
generated/<env>/htpasswd
generated/<env>/htpasswd.credentials
```

`htpasswd.credentials` создаётся только при первичной генерации `htpasswd`. Повторный `init` не пересоздаёт пароль, если `htpasswd` уже существует и не пустой.

---

## Runtime generation

```text
config/project.yml
config/apps.yml
config/routes.yml
env/common.env
env/<env>.env
        |
        v
scripts/init.py / scripts/generate-config.py
        |
        v
generated/<env>/
  apps.env
  routes.env
  stack.env
  deploy.env
  compose.frontends.yml
  nginx.conf
  htpasswd
  htpasswd.credentials
  manifest.env
        |
        v
docker compose --env-file generated/<env>/deploy.env
```

`generated/<env>/manifest.env` хранит hashes source/generated файлов. Preflight использует manifest, чтобы поймать stale runtime config до запуска контейнеров.

---

## CI/CD и operations

```text
GitHub Actions
  -> self-hosted runner
  -> repository checkout/update
  -> init / generated runtime config
  -> preflight
  -> docker compose up
  -> smoke
```

Основные проверки:

* `stack preflight` проверяет env, generated files, Docker, network, compose и nginx config.
* `doctor` проверяет host-level готовность: Docker, Compose, Tailscale, DNS, certs, ports, storage, disk, firewall.
* `stack smoke` проверяет уже запущенный стек через compose, service health и HTTPS routes.
* `security audit` проверяет hardening, published ports, management routes и секреты в tracked files.

---

## Security boundaries

```text
Internet / Tailnet / VPN
        |
        v
Nginx Edge
        |
        v
Internal Docker network
```

Границы безопасности:

* наружу публикуется только nginx (`HTTP_PORT` / `HTTPS_PORT`);
* data services остаются внутри Docker network;
* management routes `seq` и `aspire` доступны только в dev и закрыты Basic Auth;
* Tailscale ACL должен ограничивать SSH и service access;
* RadminVPN остаётся compatibility path, а не основной новый слой доступа;
* CoreDNS on Windows используется только как optional DNS endpoint;
* cert/key, env-файлы и generated credentials не должны попадать в публичный доступ.

---

## Связанные документы

* [Dev-доступ и сети](networking.md)
* [Runtime-конфигурация](runtime-config.md)
* [Operations и запуск стека](operations.md)
* [CLI и команды](commands.md)
* [CI/CD и self-hosted runner](cicd.md)
* [Репозиторий, роли и безопасность](repository.md)
