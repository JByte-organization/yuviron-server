# Yuviron Server Infrastructure & CI/CD

Инфраструктурный репозиторий для развёртывания **dev-окружения Yuviron** и автоматизации **CI/CD через GitHub Actions**.

Репозиторий содержит Docker-инфраструктуру, edge nginx, DNS-конфигурацию, SSL-сертификаты и инструменты для автоматического деплоя среды разработки.

После завершения настройки проект должен быть доступен по адресам:

```
https://dev.yuviron.com
https://api-dev.yuviron.com
```

Дополнительные API endpoints:

```
https://api-dev.yuviron.com/swagger/
https://api-dev.yuviron.com/health/
```

В дальнейшем production окружение будет доступно по адресам:

```
https://yuviron.com
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
* **Edge Nginx** — принимает HTTPS запросы извне
* **CoreDNS** — выполняет разрешение доменов dev-окружения
* **mkcert / локальный Root CA** — создаёт доверенные SSL сертификаты
* **RadminVPN** — обеспечивает сетевой доступ разработчиков
* **Windows host** — DNS сервер и прокси
* **Ubuntu VM** — сервер разработки с контейнерами
* **GitHub Actions** — CI/CD и автодеплой
* **Self-Hosted Runner** — выполняет workflow непосредственно на сервере разработки

Схема работы инфраструктуры:

```
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
yuviron-dev-nginx
        ↓
frontend / backend
```

---

# Архитектура CI/CD

Для автоматизации сборки и деплоя используется **GitHub Actions с self-hosted runner**.

Runner установлен на сервере разработки и выполняет workflow напрямую внутри инфраструктуры.

```
GitHub Repository
        ↓
GitHub Actions Workflow
        ↓
Self-Hosted Runner (Ubuntu VM)
        ↓
Docker / Docker Compose
        ↓
Обновление dev окружения
```

Runner расположен на сервере:

```
/opt/actions-runner
```

Все workflow внутри организации **JByte-organization** могут использовать этот runner.

---

# Требования к серверу

Рекомендуемые параметры виртуальной машины:

```
OS: Ubuntu 22.04 LTS
CPU: 4 cores
RAM: 8 GB
Disk: 40 GB
Docker: 24+
Docker Compose Plugin
Architecture: x64
```

Репозиторий рекомендуется размещать в директории:

```
/opt/yuviron-server
```

Self-hosted runner должен запускаться:

```
User: обычный пользователь (НЕ root)
```

Запуск runner от `root` **не рекомендуется**.

---

# Подготовка Ubuntu VM

## Создание виртуальной машины

Создать сервер на Ubuntu 22.04 и подключиться к нему по SSH.

---

## Клонирование репозитория

```
cd /opt
git clone https://github.com/JByte-organization/yuviron-server
cd yuviron-server
```

Каталог `/opt` используется для размещения сторонних сервисов и инфраструктурных проектов.

---

# Настройка переменных окружения

Перед запуском инфраструктуры необходимо создать файл конфигурации.

В репозитории находится пример:

```
env/env.example
```

Создать файл:

```
env/dev.env
```

Самый простой способ:

```
cp env/env.example env/dev.env
```

После этого открыть файл и указать значения переменных.

Пример конфигурации:

```
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

```
/opt/yuviron-server/scripts/docker_install.sh
```

Запуск:

```
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

После добавления пользователя необходимо **перелогиниться**.

---

# Генерация SSL сертификатов

Для HTTPS используется **mkcert**.

Скрипт:

```
/opt/yuviron-server/scripts/regen-yuviron-certs.sh
```

Запуск:

```
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

```
~/rootCA.crt
```

---

# Назначение rootCA.crt

Файл

```
rootCA.crt
```

является локальным **Root Certificate Authority**.

Если установить его разработчикам, браузер будет доверять сертификатам проекта.

HTTPS будет работать без предупреждений безопасности.

---

# Передача сертификатов разработчикам

Администратор должен передать:

```
rootCA.crt
radmin_setup.bat
```

---

# Установка сертификата на Windows

1. Открыть `rootCA.crt`
2. Нажать **Install Certificate**
3. Выбрать **Local Machine**
4. Выбрать хранилище

```
Trusted Root Certification Authorities
```

5. Завершить установку

---

# Настройка CoreDNS

Создать директорию:

```
C:\coredns
```

Распаковать `coredns.exe`.

Создать файл:

```
C:\coredns\Corefile
```

Конфигурация:

```
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

```
cd C:\coredns
coredns.exe -conf Corefile
```

---

# Открытие DNS порта

```
netsh advfirewall firewall add rule name="DNS TCP" dir=in action=allow protocol=TCP localport=53
```

```
netsh advfirewall firewall add rule name="DNS UDP" dir=in action=allow protocol=UDP localport=53
```

---

# Настройка portproxy

```
netsh interface portproxy add v4tov4 listenport=80 listenaddress=26.240.80.131 connectport=80 connectaddress=192.168.147.128
```

```
netsh interface portproxy add v4tov4 listenport=443 listenaddress=26.240.80.131 connectport=443 connectaddress=192.168.147.128
```

Windows принимает HTTP/HTTPS трафик и пересылает его на Ubuntu VM.

---

# Настройка DNS у разработчиков

Указать DNS сервер:

```
26.240.80.131
```

Очистить кеш:

```
ipconfig /flushdns
```

Проверка:

```
nslookup dev.yuviron.com
```

---

# Запуск инфраструктуры

Создать docker сеть:

```
docker network create yuviron_shared
```

---

Запустить сервисы:

```
cd /opt/yuviron-server/infra
docker compose -f compose.dev.yml up -d
```

---

Запустить edge nginx:

```
cd /opt/yuviron-server/edge
docker compose up -d
```

---

# Проверка контейнеров

```
docker ps --format "{{.Names}}"
```

Ожидаемый результат:

```
yuviron-edge-nginx
yuviron-dev-backend
yuviron-dev-redis
yuviron-dev-frontend
yuviron-dev-mysql
```

---

# Автодеплой через GitHub Actions

В инфраструктуре используется **self-hosted GitHub Actions runner**, который запускается на сервере разработки.

Это позволяет автоматически:

* запускать CI задачи
* собирать проект
* выполнять автодеплой dev-окружения
* не использовать платные GitHub runners

---

# Создание self-hosted runner

Runner создаётся на уровне организации.

Открыть:

```
GitHub → Organization → Settings → Actions → Runners
```

Нажать:

```
New runner
```

Выбрать:

```
Linux
Architecture: x64
```

GitHub сгенерирует инструкции установки.

---

# Установка runner

Подключиться к серверу разработки.

```
cd /opt
mkdir actions-runner
cd actions-runner
```

Скачать runner:

```
curl -o actions-runner-linux-x64.tar.gz -L https://github.com/actions/runner/releases/latest/download/actions-runner-linux-x64.tar.gz
```

Распаковать:

```
tar xzf actions-runner-linux-x64.tar.gz
```

---

# Настройка runner

GitHub выдаёт команду конфигурации.

Пример:

```
./config.sh --url https://github.com/JByte-organization --token <TOKEN>
```

Скрипт задаст вопросы:

```
Enter the name of runner
Enter runner group
Enter labels
```

Можно оставить значения по умолчанию.

---

# Запуск runner

```
./run.sh
```

---

# Запуск runner как сервиса

Чтобы runner запускался автоматически:

```
sudo ./svc.sh install
sudo ./svc.sh start
```

Проверка статуса:

```
sudo ./svc.sh status
```

---

# Проверка runner

```
GitHub → Organization → Settings → Actions → Runners
```

Runner должен иметь статус:

```
Online
```

---

# Использование runner в workflow

Чтобы GitHub Actions использовал self-hosted runner:

```
runs-on: self-hosted
```

Пример workflow:

```
jobs:
  deploy:
    runs-on: self-hosted

    steps:
      - uses: actions/checkout@v4

      - name: Build containers
        run: docker compose build

      - name: Restart services
        run: docker compose up -d
```
Примечание: готовые файлы workflow уже присутствуют в репозитории и находятся в директории:
`yuviron-server/workflows`

---

# Требования для runner

Self-hosted runner выполняет команды **непосредственно на сервере разработки**, поэтому он должен запускаться от **обычного пользователя**, а не от `root`.

Пользователь runner должен:

* запускаться **не от root**
* иметь доступ к Docker
* входить в группу `docker`

Проверить группы пользователя:

```
groups
```

Если пользователь не входит в группу `docker`, добавить его:

```
sudo usermod -aG docker $USER
```

После этого необходимо **перелогиниться**, чтобы новые права вступили в силу.

---

# Права на директорию runner

Директория runner должна принадлежать пользователю, от имени которого он запускается.

Например, если runner запускается от пользователя `nf`, необходимо назначить владельца директории:

```
sudo chown -R nf:nf /opt/actions-runner
```

Это гарантирует, что runner сможет:

* запускать workflow
* записывать данные в каталог `_work`
* обновляться без ошибок прав доступа

Если runner был установлен от имени нужного пользователя, дополнительная настройка прав может не потребоваться.

---

# Структура репозитория

```
yuviron-server
│
├ env
│   ├ env.example
│   └ dev.env
│
├ scripts
│   ├ docker_install.sh
│   └ regen-yuviron-certs.sh
│
├ infra
│   └ compose.dev.yml
│
├ edge
│   └ nginx
│
└ README.md
```

---

# Расположение runner

```
/opt
├ actions-runner
│  ├ bin
│  ├ externals
│  ├ _work
│  ├ config.sh
│  ├ run.sh
│  └ svc.sh
│
└ yuviron-server
```

---

# Полезные команды

Просмотр контейнеров:

```
docker ps
```

Просмотр логов:

```
docker logs <container>
```

Перезапуск контейнеров:

```
docker compose restart
```

Остановка инфраструктуры:

```
docker compose down
```

---

# Роли

## Администратор

1. Устанавливает Docker
2. Настраивает env файл
3. Генерирует сертификаты
4. Настраивает CoreDNS
5. Настраивает portproxy
6. Запускает инфраструктуру
7. Настраивает GitHub runner
8. Передаёт разработчикам rootCA

---

## Разработчик

1. Подключается к **RadminVPN**
2. Устанавливает `rootCA.crt`
3. Указывает DNS

```
26.240.80.131
```

4. Очищает DNS кеш

```
ipconfig /flushdns
```

5. Открывает

```
https://dev.yuviron.com
```

---

# Безопасность

Файл `rootCA.crt` должен распространяться **только внутри команды разработки**.

---

# Лицензия

Internal infrastructure repository.
Используется исключительно для разработки Yuviron.
