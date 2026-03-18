# Yuviron Server Infrastructure & CI/CD

Инфраструктурный репозиторий для развёртывания **dev-окружения Yuviron** и автоматизации **CI/CD через GitHub Actions**.

Репозиторий содержит Docker-инфраструктуру, edge nginx, DNS-конфигурацию, SSL-сертификаты и инструменты для автоматического деплоя среды разработки.

После завершения настройки dev-окружение должно быть доступно по адресам:

```text
https://dev.yuviron.com
https://dev-backoffice.yuviron.com
https://dev-admin.yuviron.com

https://api-dev.yuviron.com
```

Дополнительные API endpoints:

```text
https://api-dev.yuviron.com/swagger/
https://api-dev.yuviron.com/health/
```

В дальнейшем production-окружение планируется по адресам:

```text
https://yuviron.com
https://backoffice.yuviron.com
https://admin.yuviron.com

https://api.yuviron.com
```

---

# Назначение репозитория

Данный репозиторий используется для развёртывания и поддержки **локальной инфраструктуры разработки Yuviron**.

Документ включает:

1. Подготовку сервера разработки
2. Установку Docker
3. Настройку переменных окружения
4. Генерацию SSL сертификатов
5. Настройку внутреннего DNS
6. Настройку сетевой маршрутизации
7. Запуск dev-окружения
8. Настройку автодеплоя через GitHub Actions
9. Подключение разработчиков к инфраструктуре

---

# Архитектура инфраструктуры

Dev-окружение Yuviron доступно разработчикам через **RadminVPN** и использует отдельную инфраструктуру.

Основные компоненты системы:

* **Docker** — запускает сервисы проекта в контейнерах
* **Docker Compose** — управляет инфраструктурой контейнеров
* **Edge Nginx** — принимает HTTPS-запросы извне
* **CoreDNS** — выполняет разрешение доменов dev-окружения
* **mkcert / локальный Root CA** — создаёт доверенные SSL-сертификаты
* **RadminVPN** — обеспечивает сетевой доступ разработчиков
* **Windows host** — DNS-сервер и прокси
* **Ubuntu VM** — сервер разработки с контейнерами
* **GitHub Actions** — CI/CD и автодеплой
* **Self-Hosted Runner** — выполняет workflow непосредственно на сервере разработки

В dev-окружении используются отдельные frontend-приложения:

* **client-app** — клиентская часть
* **backoffice** — backoffice-интерфейс
* **admin** — административная панель
* **backend** — API и серверная логика

Схема работы инфраструктуры:

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
Ubuntu VM (192.168.147.128)
        ↓
yuviron-edge-nginx
        ↓
client-app / backoffice / admin / backend
```

---

# Архитектура CI/CD

Для автоматизации сборки и деплоя используется **GitHub Actions с self-hosted runner**.

Runner установлен на сервере разработки и выполняет workflow напрямую внутри инфраструктуры.

```text
GitHub Repository
        ↓
GitHub Actions Workflow
        ↓
Self-Hosted Runner (Ubuntu VM)
        ↓
Определение affected apps
        ↓
Docker / Docker Compose / app deploy scripts
        ↓
Обновление dev-окружения
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
OS: Ubuntu 22.04 LTS
CPU: 4 cores
RAM: 8 GB
Disk: 40 GB
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

# Подготовка Ubuntu VM

## Создание виртуальной машины

Создать сервер на Ubuntu 22.04 и подключиться к нему по SSH.

---

## Клонирование репозитория

```bash
cd /opt
git clone https://github.com/JByte-organization/yuviron-server
cd yuviron-server
```

Каталог `/opt` используется для размещения сторонних сервисов и инфраструктурных проектов.

---

# Настройка переменных окружения

Перед запуском инфраструктуры необходимо создать файл конфигурации.

В репозитории находится пример:

```text
env/env.example
```

Создать файл:

```text
env/dev.env
```

Самый простой способ:

```bash
cp env/env.example env/dev.env
```

После этого открыть файл и указать значения переменных.

Пример конфигурации:

```env
MYSQL_ROOT_PASSWORD=root
MYSQL_DATABASE=yuviron_dev
MYSQL_USER=yuviron
MYSQL_PASSWORD=yuviron

ASPNETCORE_ENVIRONMENT=Development
ConnectionStrings__Default=server=yuviron-dev-mysql;port=3306;database=yuviron_dev;user=yuviron;password=yuviron;
ConnectionStrings__Redis=yuviron-dev-redis:6379
Swagger__Enabled=true

FILE_STORAGE_ROOT=/var/yuviron/storage
```

Главное условие — **ключи должны совпадать с `env.example`**.

---

# Установка Docker

Для установки Docker используется скрипт:

```text
/opt/yuviron-server/scripts/docker_install.sh
```

Запуск:

```bash
cd /opt/yuviron-server/scripts
chmod +x docker_install.sh
./docker_install.sh
```

Скрипт автоматически:

* проверяет доступность DNS
* устанавливает Docker Engine
* устанавливает Docker Compose Plugin
* создаёт группу `docker`
* предлагает добавить пользователей в группу `docker`

После добавления пользователя необходимо перелогиниться.

---

# Генерация SSL сертификатов

Для HTTPS используется **mkcert**.

Скрипт:

```text
/opt/yuviron-server/scripts/regen-yuviron-certs.sh
```

Запуск:

```bash
cd /opt/yuviron-server/scripts
chmod +x regen-yuviron-certs.sh
./regen-yuviron-certs.sh
```

Скрипт выполняет:

1. проверку mkcert
2. установку mkcert
3. создание Root CA
4. генерацию сертификатов
5. копирование сертификатов в nginx

После выполнения появится файл:

```text
~/rootCA.crt
```

Сертификат должен покрывать dev-домены проекта, включая:

```text
dev.yuviron.com
dev-backoffice.yuviron.com
dev-admin.yuviron.com
api-dev.yuviron.com
```

---

# Назначение rootCA.crt

Файл

```text
rootCA.crt
```

является локальным **Root Certificate Authority**.

Если установить его разработчикам, браузер будет доверять сертификатам проекта.

HTTPS будет работать без предупреждений безопасности.

---

# Передача сертификатов разработчикам

Администратор должен передать:

```text
rootCA.crt
radmin_setup.bat
```

---

# Установка сертификата на Windows

1. Открыть `rootCA.crt`
2. Нажать **Install Certificate**
3. Выбрать **Local Machine**
4. Выбрать хранилище

```text
Trusted Root Certification Authorities
```

5. Завершить установку

---

# Настройка CoreDNS

Создать директорию:

```text
C:\coredns
```

Распаковать `coredns.exe`.

Создать файл:

```text
C:\coredns\Corefile
```

Конфигурация:

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

---

# Запуск CoreDNS

```powershell
cd C:\coredns
coredns.exe -conf Corefile
```

---

# Открытие DNS порта

```powershell
netsh advfirewall firewall add rule name="DNS TCP" dir=in action=allow protocol=TCP localport=53
```

```powershell
netsh advfirewall firewall add rule name="DNS UDP" dir=in action=allow protocol=UDP localport=53
```

---

# Настройка portproxy

```powershell
netsh interface portproxy add v4tov4 listenport=80 listenaddress=26.240.80.131 connectport=80 connectaddress=192.168.147.128
```

```powershell
netsh interface portproxy add v4tov4 listenport=443 listenaddress=26.240.80.131 connectport=443 connectaddress=192.168.147.128
```

Windows принимает HTTP/HTTPS-трафик и пересылает его на Ubuntu VM.

---

# Настройка DNS у разработчиков

Указать DNS-сервер:

```text
26.240.80.131
```

Очистить кеш:

```powershell
ipconfig /flushdns
```

Проверка:

```powershell
nslookup dev.yuviron.com
nslookup dev-backoffice.yuviron.com
nslookup dev-admin.yuviron.com
nslookup api-dev.yuviron.com
```

---

# Запуск инфраструктуры

Создать docker-сеть:

```bash
docker network create yuviron_shared
```

Запустить сервисы:

```bash
cd /opt/yuviron-server/infra
docker compose -f compose.dev.yml up -d
```

Запустить edge nginx:

```bash
cd /opt/yuviron-server/edge
docker compose up -d
```

---

# Проверка контейнеров

```bash
docker ps --format "{{.Names}}"
```

Ожидаемый результат:

```text
yuviron-edge-nginx
yuviron-dev-backend
yuviron-dev-redis
yuviron-dev-mysql
yuviron-dev-client-app
yuviron-dev-backoffice
yuviron-dev-admin
```

Если используется отдельный migrator-контейнер, он может завершаться после успешного выполнения и не отображаться в списке активных контейнеров.

---

# Маршрутизация dev-доменов

В dev-окружении настроено следующее распределение трафика:

* `https://dev.yuviron.com` → `yuviron-dev-client-app`
* `https://dev-backoffice.yuviron.com` → `yuviron-dev-backoffice`
* `https://dev-admin.yuviron.com` → `yuviron-dev-admin`
* `https://api-dev.yuviron.com/api/` → `yuviron-dev-backend`
* `https://api-dev.yuviron.com/swagger/` → `yuviron-dev-backend`
* `https://api-dev.yuviron.com/health/` → `yuviron-dev-backend`

На frontend-доменах пути `/api/` и `/swagger/` намеренно закрыты через `404`.

На `api-dev.yuviron.com` все остальные пути, кроме разрешённых backend endpoint'ов, также возвращают `404`.

---

# Автодеплой через GitHub Actions

В инфраструктуре используется **self-hosted GitHub Actions runner**, который запускается на сервере разработки.

Это позволяет автоматически:

* запускать CI-задачи
* собирать проект
* выполнять автодеплой dev-окружения
* не использовать платные GitHub runners

Для frontend-части деплой теперь выполняется **только для затронутых приложений**.

Workflow:

1. обновляет исходники frontend-репозитория на сервере
2. устанавливает зависимости через `pnpm`
3. определяет affected apps
4. запускает деплой только для изменённых приложений

Это уменьшает лишние пересборки и ускоряет обновление dev-окружения.

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

Подключиться к серверу разработки.

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

Скрипт задаст вопросы:

```text
Enter the name of runner
Enter runner group
Enter labels
```

Можно оставить значения по умолчанию.

---

# Запуск runner

```bash
./run.sh
```

---

# Запуск runner как сервиса

Чтобы runner запускался автоматически:

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

Пример workflow:

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

Готовые workflow уже присутствуют в репозитории и находятся в директории:

```text
yuviron-server/workflows
```

---

# Требования для runner

Self-hosted runner выполняет команды **непосредственно на сервере разработки**, поэтому он должен запускаться от обычного пользователя, а не от `root`.

Пользователь runner должен:

* запускаться не от `root`
* иметь доступ к Docker
* входить в группу `docker`

Проверить группы пользователя:

```bash
groups
```

Если пользователь не входит в группу `docker`, добавить его:

```bash
sudo usermod -aG docker $USER
```

После этого необходимо перелогиниться, чтобы новые права вступили в силу.

---

# Права на директорию runner

Директория runner должна принадлежать пользователю, от имени которого он запускается.

Например, если runner запускается от пользователя `nf`, необходимо назначить владельца директории:

```bash
sudo chown -R nf:nf /opt/actions-runner
```

Это гарантирует, что runner сможет:

* запускать workflow
* записывать данные в каталог `_work`
* обновляться без ошибок прав доступа

Если runner был установлен от имени нужного пользователя, дополнительная настройка прав может не потребоваться.

---

# Универсальная сборка frontend-приложений

Frontend Dockerfile теперь поддерживает параметр `APP_NAME` и может собирать разные приложения из монорепозитория через один общий шаблон.

Пример:

```bash
docker build \
  -f infra/docker/frontend/Dockerfile \
  --build-arg APP_NAME=admin \
  ../src/yuviron-frontend
```

Для dev-окружения используются следующие приложения:

* `client-app`
* `backoffice`
* `admin`

---

# Структура репозитория

```text
yuviron-server
│
├── edge
│   ├── docker-compose.yml
│   ├── nginx.conf
│   ├── certs
│   └── nginx/
│
├── env
│   ├── env.example
│   └── dev.env
│
├── infra
│   ├── compose.dev.yml
│   └── docker/
│       ├── backend/
│       ├── frontend/
│       └── migrator/
│
├── scripts
│   ├── docker_install.sh
│   └── regen-yuviron-certs.sh
│
├── workflows
│   └── frontend/
│       └── deploy-dev.yml
│
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

Просмотр логов конкретного сервиса:

```bash
docker logs <container>
```

Перезапуск инфраструктуры:

```bash
cd /opt/yuviron-server/infra
docker compose -f compose.dev.yml restart
```

Остановка dev-сервисов:

```bash
cd /opt/yuviron-server/infra
docker compose -f compose.dev.yml down
```

Остановка edge nginx:

```bash
cd /opt/yuviron-server/edge
docker compose down
```

Проверка конфигурации nginx:

```bash
docker exec -it yuviron-edge-nginx nginx -t
```

---

# Роли

## Администратор

1. Устанавливает Docker
2. Настраивает env-файл
3. Генерирует сертификаты
4. Настраивает CoreDNS
5. Настраивает portproxy
6. Запускает инфраструктуру
7. Настраивает GitHub runner
8. Передаёт разработчикам `rootCA.crt`

---

## Разработчик

1. Подключается к **RadminVPN**
2. Устанавливает `rootCA.crt`
3. Указывает DNS-сервер

```text
26.240.80.131
```

4. Очищает DNS-кеш

```powershell
ipconfig /flushdns
```

5. Открывает нужный dev-домен:

```text
https://dev.yuviron.com
https://dev-backoffice.yuviron.com
https://dev-admin.yuviron.com
```

---

# Безопасность

Файл `rootCA.crt` должен распространяться **только внутри команды разработки**.

---

# Лицензия

Internal infrastructure repository.
Используется исключительно для разработки Yuviron.
