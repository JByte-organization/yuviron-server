# Yuviron Server Infrastructure & CI/CD

Инфраструктурный репозиторий для развёртывания **dev** и **prod** окружений Yuviron и автоматизации **CI/CD через GitHub Actions**.

Ключевые изменения и рефакторинг (апрель 2026):

- Единый `infra/compose.yml` вместо набора `compose.base/dev/prod`.
- Новый Python CLI: `scripts/cli.py` управляет всеми операциями (ранее — набор bash-скриптов).
- Команды CLI вынесены в `scripts/commands/` (например, `backup`, `stack`, `certs`, `tools`).
- Конфигурация, сгенерированная утилитами, хранится в `config/`.
- Шаблоны примеров окружений: `env/example.env` и `env/common.env` (отныне трекаются).
- Резервное копирование и восстановление реализованы как команды CLI: `./scripts/cli.py backup create|restore|verify`.

Репозиторий содержит:

- Docker-инфраструктуру (композиция сервисов в `infra/compose.yml`)
- Edge (nginx) конфигурацию и шаблоны
- Скрипты и Python CLI (`scripts/cli.py`, `scripts/commands/`)
- Инструменты preflight-проверки и smoke-тесты (`checks/`, `tests/`)
- Конфигурационные шаблоны (`templates/`) и рендеринг в `config/`
- Helpers и утилиты в `core/`

После настройки окружения будут доступны типичные хосты (зависит от DNS и routes):

## Dev

```text
https://dev.yuviron.com
https://dev-backoffice.yuviron.com
https://dev-admin.yuviron.com
https://dev-api.yuviron.com
```

Дополнительные API endpoints:

```text
https://dev-api.yuviron.com/swagger/
https://dev-api.yuviron.com/health/
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

Ключевые компоненты:

- **Docker / Docker Compose plugin** — исполнение сервисов
- **Edge Nginx** — маршрутизация входящих запросов
- **MySQL, Redis, RabbitMQ** — сервисы данных
- **Backend, Migrator, MediaWorker** — серверные процессы
- **Frontend apps** — `client-app`, `backoffice`, `admin` (разделены сборки: next/static)
- **GitHub Actions + self-hosted runner** — CI/CD

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

# Dev-доступ для разработчиков

В dev-окружении может использоваться доступ через **RadminVPN**, внутренний DNS и отдельную сетевую маршрутизацию.

В таком сценарии разработчик подключается к dev-среде через VPN, а трафик направляется на сервер разработки через Windows host и Ubuntu VM.

Схема dev-доступа:

```text
Разработчик (RadminVPN)
        ↓
DNS запрос (*.yuviron.com)
        ↓
CoreDNS (Windows host)
        ↓
26.240.80.131
        ↓
Windows portproxy
        ↓
Ubuntu VM (например 192.168.147.128)
        ↓
yuviron edge nginx
        ↓
client-app / backoffice / admin / backend
```

Такой режим особенно удобен, если:

* dev-среда не публикуется напрямую в интернет
* доступ к dev должен быть только у команды разработки
* требуется единая точка входа через VPN

Если dev окружение будет доступно по другой схеме, блок с CoreDNS / portproxy можно адаптировать под конкретную сеть.

---

# Архитектура nginx

Вместо отдельных dev/prod nginx-конфигов используется **один шаблон**:

```text
edge/nginx/default.conf.template
```

Итоговая конфигурация генерируется проектным рендерером на базе Jinja2.

Шаблоны `scripts/templates/*.j2` рендерятся через Jinja2 с помощью `scripts/core/render_nginx.py`; `scripts/generate-config.py` записывает итог в `generated/<env>/nginx.conf`, который затем монтируется в nginx-контейнер как `/etc/nginx/nginx.conf` при старте стека.

Различия между окружениями задаются через переменные окружения и сгенерированные файлы в `config/`.

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

Для установки Docker доступен скрипт-утилит в репозитории:

```text
scripts/tools/docker_install.sh
```

Запуск:

```bash
cd /opt/yuviron-server/scripts/tools
chmod +x docker_install.sh
./docker_install.sh
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

## Скрипты

```text
scripts/cli.py backup create
scripts/cli.py backup verify
tools/setup-cron.sh
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
./tools/setup-cron.sh
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

Используемые файлы монтируются в nginx через соответствующий compose override.

Если используется генерация сертификатов через скрипт, запуск:

```bash
cd /opt/yuviron-server/scripts
chmod +x regen-yuviron-certs.sh
./regen-yuviron-certs.sh
```

Если используется локальный Root CA, его необходимо установить на клиентские машины разработчиков.

---

# Генерация SSL сертификатов через mkcert

Для dev-окружения можно использовать **mkcert**.

Скрипт:

```text
scripts/regen-yuviron-certs.sh
```

Запуск:

```bash
cd /opt/yuviron-server/scripts
chmod +x regen-yuviron-certs.sh
./regen-yuviron-certs.sh
```

Скрипт обычно выполняет:

1. проверку mkcert
2. установку mkcert
3. создание Root CA
4. генерацию сертификатов
5. копирование сертификатов в нужную директорию для nginx

После выполнения может появиться файл:

```text
~/rootCA.crt
```

Сертификат должен покрывать необходимые dev-домены, например:

```text
dev.yuviron.com
dev-backoffice.yuviron.com
dev-admin.yuviron.com
dev-api.yuviron.com
```

Если требуется, можно сгенерировать и другой набор сертификатов в зависимости от реальной схемы доменов.

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
        answer "{{ .Name }} 60 IN A 26.240.80.131"
    }

    hosts {
        26.240.80.131 yuviron.com
        fallthrough
    }
}

. {
    forward . 8.8.8.8 1.1.1.1
}
```

Эта конфигурация:

* отправляет все `*.yuviron.com` на IP `26.240.80.131`
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
netsh interface portproxy add v4tov4 listenport=80 listenaddress=26.240.80.131 connectport=80 connectaddress=192.168.147.128
```

```powershell
netsh interface portproxy add v4tov4 listenport=443 listenaddress=26.240.80.131 connectport=443 connectaddress=192.168.147.128
```

Проверить:

```powershell
netsh interface portproxy show v4tov4
```

Это означает:

* Windows принимает HTTP/HTTPS на `26.240.80.131`
* затем пересылает трафик на Ubuntu VM
* Ubuntu VM отдаёт трафик в контейнер `edge nginx`

---

# Настройка DNS у разработчиков

Если dev доступен через внутренний DNS, разработчику нужно указать DNS-сервер:

```text
26.240.80.131
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
* рендеринг и синтаксис Jinja2-шаблонов nginx

Это позволяет обнаружить типовые проблемы **до запуска контейнеров**.

---

# Запуск инфраструктуры

## Dev

Запуск:

```bash
cd /opt/yuviron-server
./scripts/up-dev.sh
```

Остановка:

```bash
./scripts/down-dev.sh
```

## Prod

Запуск:

```bash
cd /opt/yuviron-server
./scripts/up-prod.sh
```

Остановка:

```bash
./scripts/down-prod.sh
```

---

# Ручной запуск через Docker Compose

При необходимости можно запускать окружения вручную через compose.

## Dev

```bash
docker compose \
  --env-file ./env/dev.env \
  -p yuviron-dev \
  -f ./infra/compose.base.yml \
  -f ./infra/compose.dev.yml \
  up -d
```

## Prod

```bash
docker compose \
  --env-file ./env/prod.env \
  -p yuviron-prod \
  -f ./infra/compose.base.yml \
  -f ./infra/compose.prod.yml \
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
  --env-file ./env/dev.env \
  -p yuviron-dev \
  -f ./infra/compose.base.yml \
  -f ./infra/compose.dev.yml \
  ps
```

Проверка compose-стека prod:

```bash
docker compose \
  --env-file ./env/prod.env \
  -p yuviron-prod \
  -f ./infra/compose.base.yml \
  -f ./infra/compose.prod.yml \
  ps
```

Ожидаемо в активном состоянии могут быть контейнеры примерно такого типа:

```text
edge nginx
mysql
redis
backend
client-app
backoffice
admin
```

Если используется отдельный `migrator`-контейнер, он может завершаться после успешного применения миграций и не отображаться в списке активных контейнеров.

---

# Маршрутизация доменов

## Dev

* `https://dev.yuviron.com` → `client-app`
* `https://dev-backoffice.yuviron.com` → `backoffice`
* `https://dev-admin.yuviron.com` → `admin`
* `https://dev-api.yuviron.com` → `backend`

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
  -f infra/docker/frontend/Dockerfile \
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
  -f infra/docker/frontend/Dockerfile \
  --build-arg APP_NAME=admin \
  ./src/yuviron-frontend
```

Это позволяет не создавать отдельный Dockerfile для каждого frontend-приложения.

---

# GitHub Actions и self-hosted runner

В инфраструктуре используется **self-hosted runner**, который запускается на сервере.

Это позволяет:

* запускать CI-задачи
* выполнять preflight-check
* собирать backend/frontend
* выполнять deploy dev/prod
* не использовать платные GitHub-hosted runners для тяжёлых задач деплоя

Для frontend-части можно реализовать деплой **только для затронутых приложений**, чтобы:

* не пересобирать весь frontend без необходимости
* ускорять обновление dev-окружения
* уменьшать лишнюю нагрузку на сервер

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

Готовые workflow обычно находятся в директории с workflow-файлами репозитория.
В текущей структуре этого репозитория см. каталог:

```text
workflows/
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
2. preflight-check
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
sudo chown -R nf:nf /opt/actions-runner
```

Это гарантирует, что runner сможет:

* запускать workflow
* записывать данные в каталог `_work`
* обновляться без ошибок прав доступа

Если runner был установлен сразу от имени нужного пользователя, дополнительная настройка прав может не потребоваться.

---

# Структура репозитория

```text
yuviron-server
│
├── certs/
├── edge/
│   ├── Dockerfile
│   └── nginx/
│       └── default.conf.template
│
├── env/
│   ├── env.example
│   ├── dev.env
│   └── prod.env
│
├── infra/
│   ├── compose.base.yml
│   ├── compose.dev.yml
│   ├── compose.prod.yml
│   └── docker/
│       ├── backend/
│       ├── frontend/
│       └── migrator/
│
├── scripts/
│   ├── cli.py
│   ├── init.py
│   ├── generate-config.py
│   └── tools/
│
├── storage/
├── workflows/
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
  --env-file ./env/dev.env \
  -p yuviron-dev \
  -f ./infra/compose.base.yml \
  -f ./infra/compose.dev.yml \
  config
```

Проверка compose-конфигурации prod:

```bash
docker compose \
  --env-file ./env/prod.env \
  -p yuviron-prod \
  -f ./infra/compose.base.yml \
  -f ./infra/compose.prod.yml \
  config
```

Проверка nginx внутри контейнера:

```bash
docker exec -it <nginx-container> nginx -t
```

Перезапуск dev:

```bash
./scripts/down-dev.sh
./scripts/up-dev.sh
```

Перезапуск prod:

```bash
./scripts/down-prod.sh
./scripts/up-prod.sh
```

Ручная остановка dev-стека:

```bash
docker compose \
  --env-file ./env/dev.env \
  -p yuviron-dev \
  -f ./infra/compose.base.yml \
  -f ./infra/compose.dev.yml \
  down
```

Ручная остановка prod-стека:

```bash
docker compose \
  --env-file ./env/prod.env \
  -p yuviron-prod \
  -f ./infra/compose.base.yml \
  -f ./infra/compose.prod.yml \
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
9. при необходимости настраивает CoreDNS и portproxy для dev
10. передаёт разработчикам `rootCA.crt`, если используется локальный Root CA

---

## Разработчик

1. подключается к корпоративной сети / VPN, если это требуется
2. получает доступ к dev-доменам
3. при необходимости устанавливает `rootCA.crt`
4. указывает внутренний DNS-сервер, если используется отдельная dev DNS-схема
5. очищает DNS-кеш
6. проверяет локальную сборку frontend перед push
7. использует dev-домены для тестирования

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
* dev-доступ через VPN и внутренний DNS предпочтительнее, если среда не должна быть общедоступной

---

# Лицензия

Internal infrastructure repository.
Используется исключительно для разработки и эксплуатации Yuviron.
