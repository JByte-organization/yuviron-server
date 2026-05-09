# Yuviron Server Infrastructure

Инфраструктурный репозиторий для развёртывания **dev** и **prod** окружений Yuviron и автоматизации CI/CD через GitHub Actions.

Текущее состояние проекта:

- Единый `infra/compose.yml` вместо набора `compose.base/dev/prod`.
- Новый Python CLI: `scripts/cli.py` управляет всеми операциями (ранее — набор bash-скриптов).
- Команды CLI вынесены в `scripts/commands/` (например, `backup`, `stack`, `certs`, `tools`).
- Source of truth хранится в `config/` и `env/`, runtime-файлы генерируются в `generated/<env>/`.
- Шаблоны примеров окружений: `env/example.env` и `env/common.env` (отныне трекаются).
- Резервное копирование и восстановление реализованы как команды CLI: `./scripts/cli.py backup create|restore|verify`.
- Docker disk cleanup доступен через `./scripts/cli.py tools docker-clean`.
- Проект поддерживает оба сетевых слоя: RadminVPN для legacy-совместимости и Tailscale как предпочтительное современное решение для private networking, internal HTTPS и production-like изолированной инфраструктуры.

Репозиторий содержит:

- Docker-инфраструктуру (композиция сервисов в `infra/compose.yml`)
- Edge (nginx) конфигурацию и шаблоны
- Скрипты и Python CLI (`scripts/cli.py`, `scripts/commands/`)
- Инструменты preflight-проверки и smoke-тесты (`checks/`, `tests/`)
- Конфигурационные шаблоны (`scripts/templates/`) и рендеринг в `generated/<env>/`
- Helpers и утилиты в `scripts/core/`

После настройки окружения будут доступны типичные хосты (зависит от DNS и routes):

## Dev

```text
https://dev.yuviron.com
https://dev-backoffice.yuviron.com
https://dev-admin.yuviron.com
https://dev-aspire.yuviron.com/
https://dev-seq.yuviron.com/
https://dev-api.yuviron.com
```

Дополнительные API endpoints:

```text
https://dev-api.yuviron.com/swagger/
https://dev-api.yuviron.com/health/
https://dev-api.yuviron.com/health/ready
https://dev-api.yuviron.com/health/live
```

## Prod

```text
https://yuviron.com
https://backoffice.yuviron.com
https://admin.yuviron.com
https://api.yuviron.com
```

---

# Назначение репозитория

Данный репозиторий используется для развёртывания и поддержки инфраструктуры Yuviron.

Основная цель репозитория — реализовать **единую инфраструктурную схему**, в которой:

* dev и prod не являются двумя полностью разными системами
* существует один общий базовый слой инфраструктуры
* различия между окружениями задаются только через конфигурацию

Репозиторий используется для:

1. подготовки сервера
2. установки Docker
3. настройки переменных окружения
4. настройки SSL-сертификатов
5. настройки сетевой маршрутизации
6. запуска dev/prod окружения
7. preflight-проверки перед запуском
8. настройки self-hosted GitHub Actions runner
9. настройки CI/CD и автодеплоя
10. подключения разработчиков к dev-инфраструктуре

---

# Архитектура инфраструктуры

Инфраструктура теперь описана единым файлом `infra/compose.yml`. Различия между окружениями задаются через env-файлы и сгенерированные конфиги.
Общие переменные окружения для `.NET`-сервисов в compose вынесены в YAML anchor `x-dotnet-env`, чтобы `backend` и `media-worker` не расходились при изменениях.

Ключевые компоненты:

- **Docker / Docker Compose plugin** — исполнение сервисов
- **Edge Nginx** — маршрутизация входящих запросов
- **MySQL, Redis, RabbitMQ** — сервисы данных
- **Backend, Migrator, MediaWorker** — серверные процессы
- **Frontend apps** — `client-app`, `backoffice`, `admin` (разделены сборки: next/static)
- **GitHub Actions + self-hosted runner** — CI/CD
- **CoreDNS / RadminVPN / Tailscale** — приватный dev-доступ, legacy-совместимость и изолированный Tailnet-доступ к Linux VM

Схема работы (упрощённо):

```text
Пользователь / разработчик
  ↓
DNS (*.yuviron.com)
  ↓
edge nginx
  ↓
приложения (frontend / backend)
```
---

# 🚀 CLI (scripts/cli.py)

Основной инструмент управления инфраструктурой — Python CLI:

```bash
./scripts/cli.py <command> <subcommand> [options]
```

CLI является единым интерфейсом для работы со стеком (dev/prod), заменяя разрозненные bash-скрипты.

---

# 📦 Общая структура

```bash
./scripts/cli.py <group> <action> [env]
```

* `group` — логическая группа (stack, backup, certs, tools)
* `action` — операция
* `env` — окружение (`dev`, `prod`)

---

# 🧱 STACK

## Preflight (обязательная проверка)

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
* соответствие upstream services из `routes.env` сервисам полной compose-конфигурации

---

## Запуск

```bash
./scripts/cli.py stack up dev
./scripts/cli.py stack up prod
```

---

## Остановка

```bash
./scripts/cli.py stack down dev
./scripts/cli.py stack down prod
```

---

## Перезапуск

```bash
./scripts/cli.py stack down dev
./scripts/cli.py stack up dev
```

---

## Smoke

```bash
./scripts/cli.py stack smoke dev
./scripts/cli.py stack smoke prod
```

---

# 💾 BACKUP

## Создание

```bash
./scripts/cli.py backup create
```

## Проверка восстановления

```bash
./scripts/cli.py backup verify
./scripts/cli.py backup verify --full
```

## Полный цикл

```bash
./scripts/cli.py backup create && ./scripts/cli.py backup verify
```

---

# 🔐 CERTS

## Генерация

```bash
./scripts/cli.py certs generate --env dev --domain yuviron.com
./scripts/cli.py certs generate --env prod --domain yuviron.com
```

---

# 🛠 TOOLS

## Установка Docker

```bash
./scripts/cli.py tools docker-install
```

## Генерация конфигов

```bash
python3 scripts/generate-config.py --env dev --domain yuviron.com --apps admin,backoffice
```

## Инициализация

```bash
python3 scripts/init.py --env dev --domain yuviron.com
```

## Docker disk cleanup

```bash
./scripts/cli.py tools docker-clean --mode report
./scripts/cli.py tools docker-clean --mode build-cache --reserved-space 10gb
```

## Остальные tools

```bash
./scripts/cli.py tools check-frontend-fast
./scripts/cli.py tools seq-hash
./scripts/cli.py tools setup-cron
./scripts/cli.py tools cleanup
```

`tools cleanup` — широкий legacy cleanup script с Docker/logs/apt/tmp/generated/certs/.tmp. Для обычной Docker-очистки используй `tools docker-clean`.

---

# Generated runtime files

`scripts/init.py` и `scripts/generate-config.py` создают runtime-файлы в `generated/<env>/`:

```text
generated/<env>/
├── apps.env
├── compose.frontends.yml
├── deploy.env
├── manifest.env
├── nginx.conf
├── routes.env
└── stack.env
```

Назначение:

* `deploy.env` — итоговый env-file для Docker Compose
* `routes.env` — финальные маршруты `route|host|service:port`
* `compose.frontends.yml` — optional frontend services
* `nginx.conf` — готовый nginx config
* `stack.env` — runtime-значения стека, включая `COMPOSE_PROJECT_NAME`, пути storage/certs и host-порты nginx
* `manifest.env` — hashes source/generated файлов для проверки свежести

Если preflight пишет, что source stale или modified, перегенерируй файлы:

```bash
python3 scripts/init.py --env dev --domain yuviron.com --no-up
```

---

# 🔍 Сценарии

## Первый запуск (dev)

```bash
cp env/example.env env/dev.env

python3 scripts/init.py --env dev --domain yuviron.com --no-up
./scripts/cli.py stack preflight dev
./scripts/cli.py stack up dev
./scripts/cli.py stack smoke dev
```

---

## Первый запуск (prod)

```bash
cp env/example.env env/prod.env

python3 scripts/init.py --env prod --domain yuviron.com --no-up
./scripts/cli.py stack preflight prod
./scripts/cli.py stack up prod
./scripts/cli.py stack smoke prod
```

---

## Обновление (deploy)

```bash
git pull

./scripts/cli.py stack preflight dev
./scripts/cli.py stack up dev
./scripts/cli.py stack smoke dev
```

---

## Восстановление после сбоя

```bash
./scripts/cli.py stack down prod
./scripts/cli.py backup verify
./scripts/cli.py stack up prod
```

---

## Docker disk cleanup

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

## Hard reset

```bash
./scripts/cli.py stack down dev
./scripts/cli.py tools docker-clean --mode safe

./scripts/cli.py stack up dev
```

---

# ⚠️ Best Practices

* Всегда запускать `preflight` перед `up`
* Проверять env перед запуском prod
* Делать backup перед обновлениями
* Использовать `verify`, а не только `create`
* Не запускать CLI от root без необходимости

Исключение в compose: `seq` запускается с `user: "0:0"`, потому что образ `datalust/seq` должен писать в bind-mounted `/data`.
Это осознанное исключение; если используемый образ Seq начнёт стабильно поддерживать non-root запуск, можно заранее `chown`-нуть storage-директорию и убрать root-user.

---

# Dev-доступ для разработчиков

Dev-инфраструктура поддерживает два сетевых слоя доступа:

* **RadminVPN** — legacy-совместимость для существующих рабочих мест и текущей Windows/CoreDNS схемы.
* **Tailscale** — альтернативный приватный слой доступа к Linux VM на базе Zero Trust networking.

Tailscale не является полной заменой RadminVPN в этой инфраструктуре. Совместимость с RadminVPN сохраняется, потому что часть команды и существующих маршрутов может продолжать использовать старую схему. При этом Tailscale рекомендуется как основной современный способ доступа, особенно для новых устройств, мобильного доступа и сценариев, где не хочется поддерживать `.bat` route scripts или вручную перенастраивать RadminVPN.

Архитектурные границы:

* CoreDNS остаётся на Windows host.
* RadminVPN остаётся optional/legacy access method.
* Tailscale работает как private access layer для Linux VM.
* Tailscale daemon запускается изолированно внутри Linux VM.
* Tailscale не устанавливается на Windows host как основной VPN-слой проекта.
* Tailscale networking изолирован на уровне VM-инфраструктуры.
* Входной трафик к приложениям по Tailnet направляется напрямую на `dev-vm`.

Legacy-схема через RadminVPN:

```text
Разработчик (RadminVPN)
        ↓
DNS запрос (*.yuviron.com)
        ↓
CoreDNS (Windows host)
        ↓
<RADMIN_VPN_IP>
        ↓
Windows portproxy
        ↓
Ubuntu VM (например <UBUNTU_VM_LAN_IP>)
        ↓
yuviron edge nginx
        ↓
client-app / backoffice / admin / backend
```

Схема через Tailscale:

```text
Разработчик / мобильное устройство (Tailscale)
        ↓
Tailnet
        ↓
Linux VM / dev-vm
        ↓
yuviron edge nginx
        ↓
client-app / backoffice / admin / backend
```

Такой режим особенно полезен, если:

* dev-среда не публикуется напрямую в интернет
* доступ к dev должен быть только у команды разработки
* нужен прямой доступ с мобильных устройств
* нужно уменьшить количество ручной сетевой настройки
* нужно убрать зависимость от сложных `.bat` route scripts
* нужна среда, более похожая на production private networking

Практический эффект от Tailscale:

* проще onboarding новых устройств
* чище и понятнее сетевой контур
* меньше ручного route management
* меньше конфликтов DNS при отключённом MagicDNS resolver на VM
* изолированная VM-сетевая зона
* прямой private-доступ к dev без публикации сервисов в интернет

Если dev окружение будет доступно по другой схеме, блоки CoreDNS / portproxy / Tailnet ACL можно адаптировать под конкретную сеть.

---

# Архитектура nginx

Вместо отдельных dev/prod nginx-конфигов используется набор шаблонов:

```text
scripts/templates/01-global.conf.j2
scripts/templates/02-ssl-defaults.conf.j2
scripts/templates/03-routes.conf.j2
scripts/templates/proxy-params.conf
```

Шаблоны `scripts/templates/*.j2` рендерятся через Jinja2 с помощью `scripts/core/render_nginx.py`; `scripts/generate-config.py` записывает итог в `generated/<env>/nginx.conf`, который затем монтируется в nginx-контейнер как `/etc/nginx/nginx.conf` при старте стека.

Различия между окружениями задаются через `env/<env>.env`, `env/common.env`, `config/routes.yml`, `config/apps.yml` и сгенерированные файлы в `generated/<env>/`.

Финальные маршруты nginx берёт не напрямую из `config/routes.yml`, а из `generated/<env>/routes.env`.

В `infra/compose.yml` nginx ждёт готовности `backend` и `client-app` через `depends_on: condition: service_healthy`.
Для `client-app` healthcheck проверяет TCP-порт Next.js внутри контейнера, не требуя отдельного `/api/health` endpoint во frontend-репозитории.
Сам nginx также имеет Docker healthcheck: контейнер локально запрашивает `http://127.0.0.1/health`.
Этот endpoint объявлен в HTTP server-блоке до редиректа на HTTPS, поэтому проверка не зависит от TLS-сертификата и внешнего `Host`.

Такой подход позволяет:

* избежать дублирования nginx-конфигов
* использовать один source of truth
* упростить поддержку dev и prod
* уменьшить риск расхождения логики маршрутизации между окружениями

---

# Архитектура CI/CD

Для автоматизации сборки и деплоя используется **GitHub Actions с self-hosted runner**.

Runner установлен на сервере и выполняет workflow напрямую внутри инфраструктуры.

Схема работы CI/CD:

```text
GitHub Repository
        ↓
GitHub Actions Workflow
        ↓
Self-Hosted Runner
        ↓
Preflight / Build / Deploy
        ↓
Обновление dev или prod окружения
```

Runner расположен на сервере:

```text
/opt/actions-runner
```

Все workflow внутри организации **JByte-organization** могут использовать этот runner.

Актуальные shared workflow:

```text
shared/backend/deploy-dev.yml
shared/backend/deploy-prod.yml
shared/frontend/.github/workflows/deploy-dev.yml
shared/frontend/.github/workflows/deploy-prod.yml
```

Текущий deploy flow:

1. синхронизировать source repo в `src/yuviron-backend` или `src/yuviron-frontend`
2. выполнить preflight
3. собрать/поднять стек через `./scripts/cli.py stack up <env>`
4. выполнить `./scripts/cli.py stack smoke <env>`
5. показать compose status и хвосты логов
6. после успешного deploy подрезать Docker build cache через `tools docker-clean`

---

# Требования к серверу

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

Репозиторий рекомендуется размещать в директории:

```text
/opt/yuviron-server
```

Self-hosted runner должен запускаться:

```text
User: обычный пользователь (НЕ root)
```

Запуск runner от `root` не рекомендуется.

---

# Подготовка сервера

## Создание виртуальной машины

Создать сервер на Ubuntu 22.04+ и подключиться к нему по SSH.

Если используется dev-инфраструктура с внутренней сетевой схемой, необходимо заранее понимать:

* IP Ubuntu VM
* IP Windows host
* внешний VPN IP / адрес, на который будет смотреть DNS
* схему проброса портов 80/443
* Tailnet IP для `dev-vm` и `host-pc`, если используется Tailscale
* какой access layer используется для конкретного разработчика: Tailscale или RadminVPN legacy

---

## Клонирование репозитория

```bash
cd /opt
git clone https://github.com/JByte-organization/yuviron-server
cd yuviron-server
```

Каталог `/opt` используется для размещения сторонних сервисов и инфраструктурных проектов.

---

# Установка Docker

Для установки Docker доступна CLI-обёртка над `scripts/tools/docker_install.sh`:

```bash
cd /opt/yuviron-server
./scripts/cli.py tools docker-install
```

Скрипт автоматически:

* проверяет доступность DNS
* устанавливает Docker Engine
* устанавливает Docker Compose Plugin
* создаёт группу `docker`
* предлагает добавить пользователя в группу `docker`

После добавления пользователя необходимо перелогиниться.

---

# Настройка переменных окружения

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

## Edge-порты

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

## Пример dev-конфигурации

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

## Пример prod-конфигурации

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

Главное условие — **ключи должны совпадать с `env/example.env`**.

---


---

# Бэкапы

В инфраструктуре реализована система резервного копирования.

## Что покрывается

* MySQL (дампы базы)
* storage (файлы)
* единые архивы
* тест восстановления

## Переменные окружения

```env
BACKUP_ROOT=./backups
BACKUP_TMP=./backups/tmp
BACKUP_LOG_DIR=./backups/logs
BACKUP_ARCHIVE_DIR=./backups/archives
BACKUP_RESTORE_TEST_TMP=./backups/restore-test

BACKUP_ENVS=dev,prod

BACKUP_STORAGE_DEV=./storage/dev
BACKUP_STORAGE_PROD=./storage/prod

BACKUP_REMOTE_PATH=

BACKUP_RETENTION_DAYS=14

BACKUP_PROJECT_NAME=yuviron-server

COMPOSE_FILE=infra/compose.yml

COMPOSE_PROJECT_DEV=yuviron_dev
COMPOSE_PROJECT_PROD=yuviron_prod

MYSQL_SERVICE_NAME=mysql
BACKEND_SERVICE_NAME=backend
```

## Команды

```text
./scripts/cli.py backup create
./scripts/cli.py backup verify
./scripts/cli.py backup verify --full
./scripts/cli.py backup restore --env dev --archive backups/archives/<archive>.tar.gz
./scripts/cli.py tools setup-cron
```

## Создание бэкапа

```bash
./scripts/cli.py backup create
```

## Проверка восстановления

```bash
./scripts/cli.py backup verify
```

## Автоматизация (cron)

```bash
./scripts/cli.py tools setup-cron
```

## Ротация

```env
BACKUP_RETENTION_DAYS=14
```

## Структура

```text
backups/
├── tmp/
├── logs/
├── archives/
└── restore-test/
```

Директория `backups` добавлена в `.gitignore`.


# Сертификаты

Сертификаты хранятся в директории:

```text
certs/
```

Для dev и prod могут использоваться разные сертификаты.

Используемые файлы задаются в `generated/<env>/stack.env` и монтируются в nginx через единый `infra/compose.yml`.

Генерация через CLI:

```bash
./scripts/cli.py certs generate --env dev --domain yuviron.com
./scripts/cli.py certs generate --env prod --domain yuviron.com
```

После замены файлов сертификата работающий nginx должен перечитать их:

```bash
./scripts/cli.py certs reload --env dev
./scripts/cli.py certs reload --env prod
```

Команда сначала выполняет `nginx -t` внутри контейнера, затем `nginx -s reload`.
Для автоматической ротации запускай её как post-renew hook или cron-задачу после обновления `CERT_FILE` и `KEY_FILE`.

Перед генерацией сертификатов должен существовать `generated/<env>/routes.env`, поэтому сначала запускается `scripts/init.py` или `scripts/generate-config.py`.

Если используется локальный Root CA, его необходимо установить на клиентские машины разработчиков.

---

# Генерация SSL сертификатов через mkcert

CLI использует `mkcert` и SAN-список из `generated/<env>/routes.env`.

```bash
./scripts/cli.py certs generate --env dev --domain yuviron.com
```

Команда выполняет:

1. проверку mkcert
2. предложение установить `mkcert` и `libnss3-tools`, если mkcert не найден
3. создание/установку Root CA
4. генерацию сертификатов
5. копирование `rootCA.pem` в `~/rootCA.crt`

Если стек уже запущен, после перевыпуска сертификата нужно применить его в nginx:

```bash
./scripts/cli.py certs reload --env dev
```

После выполнения может появиться файл:

```text
~/rootCA.crt
```

Сертификат покрывает hosts из `routes.env`, например:

```text
dev.yuviron.com
dev-backoffice.yuviron.com
dev-admin.yuviron.com
dev-api.yuviron.com
dev-seq.yuviron.com
dev-aspire.yuviron.com
```

---

# Назначение rootCA.crt

Файл:

```text
rootCA.crt
```

является локальным **Root Certificate Authority**.

Если установить его разработчикам на рабочие машины, браузер будет доверять сертификатам dev-окружения.

HTTPS будет работать без предупреждений безопасности.

---

# Передача сертификатов разработчикам

Если dev-среда работает через локальный Root CA, администратор должен передать разработчикам:

```text
rootCA.crt
radmin_setup.bat
```

или другой набор файлов/инструкций, который используется внутри команды.

---

# Установка сертификата на Windows

1. Открыть `rootCA.crt`
2. Нажать **Install Certificate / Установить сертификат**
3. Выбрать **Local Machine / Локальный компьютер**
4. Выбрать хранилище:

```text
Trusted Root Certification Authorities / Доверенные корневые центры сертификации
```

5. Завершить установку

После этого браузер будет доверять сертификатам, подписанным этим Root CA.

---

# Настройка CoreDNS для dev-окружения

Если dev-среда работает через Windows host с отдельным DNS, можно использовать **CoreDNS**.

Создать директорию:

```text
C:\coredns
```

Распаковать туда `coredns.exe`.

Создать файл:

```text
C:\coredns\Corefile
```

Пример конфигурации:

```txt
yuviron.com {
    template IN A {
        match .*\.yuviron\.com
        answer "{{ .Name }} 60 IN A <RADMIN_VPN_IP>"
    }

    hosts {
        <RADMIN_VPN_IP> yuviron.com
        fallthrough
    }
}

. {
    forward . 8.8.8.8 1.1.1.1
}
```

Эта конфигурация:

* отправляет все `*.yuviron.com` на IP `<RADMIN_VPN_IP>`
* резолвит корневой домен `yuviron.com`
* все остальные DNS-запросы проксирует на публичные резолверы

---

# Запуск CoreDNS

```powershell
cd C:\coredns
coredns.exe -conf Corefile
```

Для production такая схема обычно не используется, но для dev внутри VPN — это нормальный вариант.

---

# Открытие DNS порта

Чтобы DNS работал на Windows host, нужно открыть входящие порты 53:

```powershell
netsh advfirewall firewall add rule name="DNS TCP" dir=in action=allow protocol=TCP localport=53
```

```powershell
netsh advfirewall firewall add rule name="DNS UDP" dir=in action=allow protocol=UDP localport=53
```

---

# Настройка portproxy

Если Windows host принимает трафик и перенаправляет его на Ubuntu VM, нужно настроить `portproxy`.

Пример:

```powershell
netsh interface portproxy add v4tov4 listenport=80 listenaddress=<RADMIN_VPN_IP> connectport=8080 connectaddress=<UBUNTU_VM_LAN_IP>
```

```powershell
netsh interface portproxy add v4tov4 listenport=443 listenaddress=<RADMIN_VPN_IP> connectport=8443 connectaddress=<UBUNTU_VM_LAN_IP>
```

Проверить:

```powershell
netsh interface portproxy show v4tov4
```

Это означает:

* Windows принимает HTTP/HTTPS на `<RADMIN_VPN_IP>`
* затем пересылает трафик на dev-порты Ubuntu VM (`8080/8443` по умолчанию)
* Ubuntu VM отдаёт трафик в контейнер `edge nginx`

Если dev окружение явно настроено на `HTTP_PORT=80` и `HTTPS_PORT=443`, используй `connectport=80/443`.

---

# Настройка DNS у разработчиков

Если dev доступен через RadminVPN/CoreDNS legacy-схему, разработчику нужно указать внутренний DNS-сервер:

```text
<RADMIN_VPN_IP>
```

Очистить кеш DNS:

```powershell
ipconfig /flushdns
```

Проверка:

```powershell
nslookup dev.yuviron.com
nslookup dev-backoffice.yuviron.com
nslookup dev-admin.yuviron.com
nslookup dev-api.yuviron.com
```

Если всё настроено правильно, домены должны резолвиться в нужный IP.

---

# Tailscale как приватный слой доступа

Tailscale используется как альтернативный private access layer для dev-инфраструктуры. Он не отменяет RadminVPN, а добавляет более современный путь доступа к Linux VM через Tailnet.

В текущей схеме:

* RadminVPN остаётся доступен для legacy-совместимости.
* Tailscale является предпочтительным способом подключения для новых устройств.
* Мобильные устройства могут обращаться к dev-проекту напрямую через Tailnet.
* Не нужно поддерживать отдельные `.bat` route scripts для доступа к VM.
* Не нужно вручную перенастраивать маршруты RadminVPN для каждого нового сценария.
* CoreDNS остаётся размещённым на Windows host.
* Tailscale daemon работает внутри Linux VM.
* Tailscale networking ограничен инфраструктурным слоем Linux VM.

Важно: Tailscale не должен описываться как полный replacement для RadminVPN. Это дополнительный private access layer, который уменьшает операционные сложности, но не ломает существующую RadminVPN-схему.

---

# Tailscale DNS / MagicDNS Issue

Реальная проблема была связана с некорректным DNS resolution внутри Linux VM.

После подключения VM к Tailnet Tailscale автоматически включил DNS management / MagicDNS и перезаписал `/etc/resolv.conf`:

```bash
nameserver 100.100.100.100
nameserver fd7a:115c:a1e0::53
```

Из-за этого VM начала резолвить внешние адреса через Tailscale DNS. В текущей инфраструктурной схеме это приводило к некорректному resolution части публичных доменов.

Симптом на уровне shell:

```bash
curl: (6) Could not resolve host
```

Любые ошибки приложений или CLI-инструментов, которым нужен доступ к внешним сервисам, в таком состоянии были следствием DNS failure. Root cause был не в авторизации конкретного инструмента, не в аккаунте и не в IDE, а в том, что VM не могла корректно резолвить нужные внешние адреса.

---

# DNS Resolution Fix

Решение: отключить автоматическое DNS management со стороны Tailscale внутри Linux VM:

```bash
sudo tailscale up --accept-dns=false --ssh
```

Эта команда не отключает Tailscale.

Она отключает только:

* автоматическую замену системного resolver
* использование MagicDNS как системного resolver

После этого Tailscale продолжает предоставлять:

* VPN connectivity
* Tailnet communication
* SSH connectivity
* private networking

После отключения DNS management:

* VM снова использует обычные внешние DNS resolvers
* публичные домены резолвятся через нормальный системный DNS path
* CLI-инструменты и сервисы, зависящие от внешнего DNS, снова работают корректно

Возможный side effect: MagicDNS hostnames могут перестать резолвиться на узлах, где отключён `accept-dns`:

* `dev-vm`
* `host-pc`

При этом прямые Tailnet IP addresses продолжают работать:

```text
100.x.x.x
```

Для инфраструктурных scripts, ACL notes и troubleshooting лучше указывать Tailnet IP явно, если MagicDNS отключён или ведёт себя нестабильно.

---

# Tailscale ACL policy

Шаблон Tailnet ACL policy:

```json
{
  "hosts": {
    "host-pc": "<HOST_PC_TAILNET_IP>",
    "dev-vm": "<DEV_VM_TAILNET_IP>"
  },

  "tagOwners": {
    "tag:dev-vm": ["<INFRA_OWNER_EMAIL>"]
  },

  "acls": [
    {
      "action": "accept",
      "src": ["autogroup:member"],
      "dst": ["host-pc:53"]
    },
    {
      "action": "accept",
      "src": ["autogroup:member"],
      "dst": [
        "dev-vm:80",
        "dev-vm:443"
      ]
    },
    {
      "action": "accept",
      "src": ["<INFRA_OWNER_EMAIL>"],
      "dst": ["tag:dev-vm:22"]
    },
    {
      "action": "accept",
      "src": ["<INFRA_OWNER_EMAIL>"],
      "dst": ["host-pc:22"]
    }
  ],

  "ssh": [
    {
      "action": "accept",
      "src": ["<INFRA_OWNER_EMAIL>"],
      "dst": ["tag:dev-vm"],
      "users": ["<LINUX_VM_USER>"]
    }
  ]
}
```

Что разрешает policy:

* участники Tailnet могут обращаться к DNS на Windows host: `host-pc:53`
* участники Tailnet могут обращаться к dev web ports на VM: `dev-vm:80` и `dev-vm:443`
* SSH к VM и Windows host ограничен владельцем инфраструктуры
* Tailscale SSH разрешён только к tagged Linux VM и только для локального пользователя `<LINUX_VM_USER>`

Если Tailnet IP меняются, блок `hosts` нужно обновить перед применением policy.
Перед применением policy нужно заменить `<HOST_PC_TAILNET_IP>`, `<DEV_VM_TAILNET_IP>`, `<INFRA_OWNER_EMAIL>` и `<LINUX_VM_USER>` на реальные значения.

---

# SSH через Tailnet

SSH-архитектура разделена по типам узлов:

* **Linux VM (`dev-vm`)** использует Tailscale SSH.
* **Windows host (`host-pc`)** использует обычный OpenSSH Server.
* SSH traffic в обоих случаях проходит через Tailnet.
* ACL rules ограничивают SSH-доступ только владельцем инфраструктуры.

Tailscale SSH включается на Linux VM через:

```bash
sudo tailscale up --accept-dns=false --ssh
```

Windows host не должен рассматриваться как основной Tailscale VPN layer проекта. Он продолжает выполнять свои инфраструктурные обязанности, включая CoreDNS и standard OpenSSH Server, а основной приватный application access через Tailscale направляется на Linux VM.

---

# Docker network

Инфраструктура использует заранее созданную внешнюю сеть:

```text
yuviron_shared
```

Создание сети:

```bash
docker network create yuviron_shared
```

Если сеть уже существует, повторное создание не требуется.

---

# Preflight-проверка

Перед запуском окружения рекомендуется выполнять preflight-проверку.

## Dev

```bash
./scripts/cli.py stack preflight dev
```

## Prod

```bash
./scripts/cli.py stack preflight prod
```

Скрипт проверяет:

* наличие обязательных файлов и директорий
* доступ к Docker
* наличие env-файла
* права на storage
* свободное место на диске
* наличие сети `yuviron_shared`
* валидность compose-конфигурации
* свежесть generated-файлов по `generated/<env>/manifest.env`
* наличие route hosts в `generated/<env>/nginx.conf`
* соответствие upstream services из `routes.env` сервисам compose
* синтаксис nginx через `nginx -t`

Это позволяет обнаружить типовые проблемы **до запуска контейнеров**.

Для проверки без вмешательства в основной compose project:

```bash
./scripts/cli.py stack preflight dev --isolated
```

---

# Запуск инфраструктуры

## Dev

Запуск:

```bash
cd /opt/yuviron-server
./scripts/cli.py stack up dev
```

Остановка:

```bash
./scripts/cli.py stack down dev
```

## Prod

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

# Ручной запуск через Docker Compose

При необходимости можно запускать окружения вручную через compose.

## Dev

```bash
docker compose \
  --env-file ./generated/dev/deploy.env \
  -f ./infra/compose.yml \
  -f ./generated/dev/compose.frontends.yml \
  -p yuviron-dev \
  up -d
```

## Prod

```bash
docker compose \
  --env-file ./generated/prod/deploy.env \
  -f ./infra/compose.yml \
  -f ./generated/prod/compose.frontends.yml \
  -p yuviron-prod \
  up -d
```

---

# Compose project names

Для развёртывания используются отдельные project names:

* `yuviron-dev`
* `yuviron-prod`

Это позволяет:

* изолировать dev и prod
* исключить конфликты имён контейнеров, сетей и volume
* независимо управлять окружениями

---

# Проверка контейнеров

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

Если используется отдельный `migrator`-контейнер, он может завершаться после успешного применения миграций и не отображаться в списке активных контейнеров.

---

# Маршрутизация доменов

## Dev

* `https://dev.yuviron.com` → `client-app`
* `https://dev-backoffice.yuviron.com` → `backoffice`
* `https://dev-admin.yuviron.com` → `admin`
* `https://dev-api.yuviron.com` → `backend`

Если ты обращаешься к Ubuntu VM напрямую без внешнего portproxy/reverse proxy, dev HTTPS будет доступен на порту `8443`, например `https://dev.yuviron.com:8443`.
При portproxy с `listenport=443` внешний URL остаётся без порта.

Дополнительные dev API endpoints:

* `https://dev-api.yuviron.com/swagger/`
* `https://dev-api.yuviron.com/health/`

Если в nginx сохранена строгая маршрутизация, то:

* на frontend-доменах пути `/api/` и `/swagger/` могут быть намеренно закрыты через `404`
* на `dev-api.yuviron.com` могут быть разрешены только backend endpoint'ы, а остальные пути возвращают `404`

## Prod

* `https://yuviron.com` → `client-app`
* `https://backoffice.yuviron.com` → `backoffice`
* `https://admin.yuviron.com` → `admin`
* `https://api.yuviron.com` → `backend`

---

# Frontend сборка

Frontend собирается из монорепозитория через единый Dockerfile с параметром `APP_NAME`.

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

# Универсальная сборка frontend-приложений

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

# GitHub Actions и self-hosted runner

В инфраструктуре используется **self-hosted runner**, который запускается на сервере.

Это позволяет:

* запускать CI-задачи
* выполнять `./scripts/cli.py stack preflight <env>`
* собирать backend/frontend
* выполнять deploy dev/prod
* не использовать платные GitHub-hosted runners для тяжёлых задач деплоя

Для frontend-части можно реализовать деплой **только для затронутых приложений**, чтобы:

* не пересобирать весь frontend без необходимости
* ускорять обновление dev-окружения
* уменьшать лишнюю нагрузку на сервер

Для ручного обновления одного frontend-сервиса можно использовать:

```bash
./shared/frontend/scripts/deploy-frontend-service.sh admin dev
```

Скрипт использует актуальную compose-схему: `infra/compose.yml`, `generated/<env>/compose.frontends.yml` и `generated/<env>/deploy.env`.

---

# Создание self-hosted runner

Runner создаётся на уровне организации.

Открыть:

```text
GitHub → Organization → Settings → Actions → Runners
```

Нажать:

```text
New runner
```

Выбрать:

```text
Linux
Architecture: x64
```

GitHub сгенерирует инструкции установки.

---

# Установка runner

Подключиться к серверу.

```bash
cd /opt
mkdir actions-runner
cd actions-runner
```

Скачать runner:

```bash
curl -o actions-runner-linux-x64.tar.gz -L https://github.com/actions/runner/releases/latest/download/actions-runner-linux-x64.tar.gz
```

Распаковать:

```bash
tar xzf actions-runner-linux-x64.tar.gz
```

---

# Настройка runner

GitHub выдаёт команду конфигурации.

Пример:

```bash
./config.sh --url https://github.com/JByte-organization --token <TOKEN>
```

Скрипт может задать вопросы:

```text
Enter the name of runner
Enter runner group
Enter labels
```

Можно оставить значения по умолчанию или указать собственные labels.

---

# Запуск runner

```bash
./run.sh
```

---

# Запуск runner как сервиса

Чтобы runner запускался автоматически после перезагрузки сервера:

```bash
sudo ./svc.sh install
sudo ./svc.sh start
```

Проверка статуса:

```bash
sudo ./svc.sh status
```

---

# Проверка runner

Открыть:

```text
GitHub → Organization → Settings → Actions → Runners
```

Runner должен иметь статус:

```text
Online
```

---

# Использование runner в workflow

Чтобы GitHub Actions использовал self-hosted runner:

```yaml
runs-on: [self-hosted, yuviron]
```

Готовые shared workflow находятся в:

```text
shared/backend/deploy-dev.yml
shared/backend/deploy-prod.yml
shared/frontend/.github/workflows/deploy-dev.yml
shared/frontend/.github/workflows/deploy-prod.yml
```

Пример шага workflow:

```yaml
jobs:
  deploy:
    runs-on: [self-hosted, yuviron]

    steps:
      - name: Update source
        run: |
          cd /opt/yuviron-server/src/yuviron-frontend
          git fetch origin dev
          git reset --hard origin/dev

      - name: Install dependencies
        run: |
          cd /opt/yuviron-server/src/yuviron-frontend
          pnpm install --frozen-lockfile

      - name: Deploy app
        run: |
          cd /opt/yuviron-server/src/yuviron-frontend
          pnpm --filter admin run deploy
```

В более полной схеме workflow может делать:

1. checkout/update исходников
2. `./scripts/cli.py stack preflight <env>`
3. build
4. affected detection
5. deploy только нужных приложений
6. перезапуск соответствующего окружения

---

# Требования для runner

Self-hosted runner должен:

* запускаться не от `root`
* иметь доступ к Docker
* входить в группу `docker`

Проверка групп пользователя:

```bash
groups
```

Если пользователь не входит в группу `docker`:

```bash
sudo usermod -aG docker $USER
```

После этого необходимо перелогиниться.

---

# Права на директорию runner

Если runner расположен в `/opt/actions-runner`, директория должна принадлежать пользователю, от имени которого он запускается.

Пример:

```bash
sudo chown -R <RUNNER_USER>:<RUNNER_USER> /opt/actions-runner
```

Это гарантирует, что runner сможет:

* запускать workflow
* записывать данные в каталог `_work`
* обновляться без ошибок прав доступа

Если runner был установлен сразу от имени нужного пользователя, дополнительная настройка прав может не потребоваться.

---

# Структура репозитория

```text
yuviron-server/
├── certs/
├── config/
│   ├── apps.yml
│   ├── project.yml
│   └── routes.yml
├── env/
│   ├── common.env
│   ├── dev.env
│   ├── example.env
│   └── prod.env
├── generated/
│   └── <env>/
├── infra/
│   ├── compose.yml
│   ├── docker/
│   └── edge/
├── scripts/
│   ├── checks/
│   ├── commands/
│   ├── core/
│   ├── templates/
│   ├── tests/
│   ├── tools/
│   ├── cli.py
│   ├── generate-config.py
│   └── init.py
├── shared/
│   ├── backend/
│   └── frontend/
├── src/
│   ├── yuviron-backend/
│   └── yuviron-frontend/
├── storage/
├── logs/
├── backups/
└── README.md
```

---

# Расположение runner

```text
/opt
├── actions-runner
│   ├── bin
│   ├── externals
│   ├── _work
│   ├── config.sh
│   ├── run.sh
│   └── svc.sh
│
└── yuviron-server
```

---

# Полезные команды

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

# Роли

## Администратор

1. устанавливает Docker
2. настраивает `env/dev.env` и `env/prod.env`
3. настраивает сертификаты
4. создаёт сеть `yuviron_shared`
5. запускает preflight-проверку
6. запускает инфраструктуру
7. настраивает GitHub runner
8. следит за CI/CD
9. при необходимости настраивает CoreDNS и portproxy для RadminVPN legacy-схемы
10. настраивает Tailscale ACL и Tailscale SSH для Linux VM, если используется Tailnet-доступ
11. передаёт разработчикам `rootCA.crt`, если используется локальный Root CA

---

## Разработчик

1. подключается через Tailscale (рекомендуется) или RadminVPN (optional/legacy), если это требуется
2. получает доступ к dev-доменам
3. при необходимости устанавливает `rootCA.crt`
4. указывает внутренний DNS-сервер, если используется отдельная RadminVPN/CoreDNS dev DNS-схема
5. очищает DNS-кеш
6. проверяет локальную сборку frontend перед push
7. использует dev-домены для тестирования

Разработчикам не нужно удалять RadminVPN, если он уже используется. Для новых подключений предпочтительнее Tailscale, потому что он проще для onboarding, работает с мобильными устройствами и не требует ручного route management.

Пример dev-доменов:

```text
https://dev.yuviron.com
https://dev-backoffice.yuviron.com
https://dev-admin.yuviron.com
https://dev-api.yuviron.com
```

---

# Безопасность

* сертификаты и ключи должны распространяться только внутри команды
* `prod.env` не должен публиковаться
* runner не должен запускаться от `root`
* доступ к Docker должен быть ограничен доверенными пользователями
* `rootCA.crt` должен распространяться только среди участников команды разработки
* dev-доступ через VPN/Tailnet и внутренний DNS предпочтительнее, если среда не должна быть общедоступной
* RadminVPN сохраняется как optional/legacy access method; новые подключения лучше заводить через Tailscale
* Tailscale ACL должен ограничивать SSH-доступ только владельцем инфраструктуры
* на Linux VM нужно держать `--accept-dns=false`, если MagicDNS ломает внешний DNS resolution

---

# Лицензия

Internal infrastructure repository.
Используется исключительно для разработки и эксплуатации Yuviron.
