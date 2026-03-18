# Yuviron Server Infrastructure

Инфраструктурный репозиторий для развёртывания **dev-окружения Yuviron**.

Репозиторий содержит Docker-инфраструктуру, edge nginx, DNS-конфигурацию и вспомогательные скрипты для запуска среды разработки.
Этот документ описывает **полный процесс подготовки и запуска dev-окружения**, а также **подключение разработчиков** к инфраструктуре через внутреннюю сеть.

После завершения настройки проект должен быть доступен по адресам:

```
https://dev.yuviron.com
https://api-dev.yuviron.com

(В дальнейшем):
https://yuviron.com
https://api.yuviron.com
```

Дополнительные API endpoints:

```
https://api-dev.yuviron.com/swagger/
https://api-dev.yuviron.com/health/
```

---

# Назначение

Данный репозиторий используется для развёртывания и поддержки **локальной инфраструктуры разработки Yuviron**.

Документ включает:

1. Установку Docker на сервере
2. Настройку конфигурационных переменных
3. Генерацию SSL сертификатов
4. Настройку локального DNS внутри сети RadminVPN
5. Настройку сетевой маршрутизации
6. Запуск dev-окружения проекта
7. Подключение разработчиков

---

# Архитектура

Dev-окружение Yuviron доступно разработчикам через **RadminVPN** и использует отдельную инфраструктуру.

Основные компоненты системы:

* **Docker** — запускает сервисы проекта в контейнерах
* **Docker Compose** — управляет инфраструктурой контейнеров
* **Edge Nginx** — принимает HTTPS-запросы извне
* **CoreDNS** — выполняет разрешение доменов dev-окружения
* **mkcert / локальный Root CA** — создаёт доверенные SSL сертификаты
* **Radmin VPN** — обеспечивает сетевой доступ разработчиков
* **Windows host** — выступает DNS сервером и прокси
* **Ubuntu VM** — сервер разработки, где запущены контейнеры

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

# Требования к серверу

Рекомендуемые параметры виртуальной машины:

```
OS: Ubuntu 22.04 LTS
CPU: 4 cores
RAM: 8 GB
Disk: 40 GB
Docker: 24+
Docker Compose Plugin
```

Репозиторий рекомендуется размещать в директории:

```
/opt/yuviron-server
```

---

# Подготовка Ubuntu VM

Перед запуском проекта необходимо подготовить сервер разработки.

## 1. Создать виртуальную машину

Создать сервер на Ubuntu 22.04 и подключиться к нему по SSH.

## 2. Клонировать репозиторий

```
cd /opt
git clone https://github.com/JByte-organization/yuviron-server
cd yuviron-server
```

Примечание:
Команда `git clone` скачивает инфраструктурный репозиторий на сервер.
Каталог `/opt` обычно используется в Linux для размещения сторонних сервисов и инфраструктурных проектов.

---

# Настройка переменных окружения

Перед запуском инфраструктуры необходимо создать файл конфигурации окружения.

В репозитории находится пример файла:

```
env/env.example
```

Нужно создать файл:

```
env/dev.env
```

Самый простой способ:

```
cp env/env.example env/dev.env
```

Примечание:
Команда `cp` создаёт копию файла `env.example`.
Файл `dev.env` используется Docker контейнерами как источник переменных окружения.

После этого открыть файл `dev.env` и указать значения переменных.

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

Главное условие — **ключи должны совпадать с `env.example`**, так как они используются Docker-сервисами.

---

# Установка Docker

Для установки Docker используется скрипт:

```
/opt/yuviron-server/scripts/docker_install.sh
```

## Запуск установки

```
cd /opt/yuviron-server/scripts
chmod +x docker_install.sh
./docker_install.sh
```

Примечание:
Команда `chmod +x` делает файл исполняемым, чтобы его можно было запускать как программу.

## Что делает скрипт

Скрипт автоматически выполняет следующие действия:

* проверяет доступность DNS перед установкой;
* устанавливает **Docker Engine**, если Docker ещё не установлен;
* устанавливает **Docker Compose Plugin**;
* создаёт группу `docker`, если она отсутствует;
* выводит интерактивное меню и предлагает выбрать пользователей, которых необходимо добавить в группу `docker`.

## Важно

* Скрипт автоматически использует `sudo`, если запущен не от пользователя `root`.
* Пользователи, добавленные в группу `docker`, смогут выполнять команды Docker **без использования `sudo`**.
* После добавления пользователя в группу `docker` необходимо **перелогиниться**, чтобы применились новые права доступа.

Если ни один пользователь не будет выбран, Docker всё равно будет установлен, однако команды Docker придётся запускать с использованием `sudo`.

---

# Генерация SSL сертификатов

Для HTTPS в dev-окружении используется **mkcert**.

## Скрипт генерации

```
/opt/yuviron-server/scripts/regen-yuviron-certs.sh
```

Скрипт выполняет:

1. Проверку установлен ли mkcert
2. Установку mkcert при необходимости
3. Создание Root CA
4. Генерацию SSL сертификатов
5. Копирование сертификатов в nginx

## Запуск

```
cd /opt/yuviron-server/scripts
chmod +x regen-yuviron-certs.sh
./regen-yuviron-certs.sh
```

Примечание:
**mkcert** создаёт локальный **Root Certificate Authority** и на его основе генерирует SSL-сертификаты.
Это позволяет использовать HTTPS в dev-окружении без предупреждений браузера о небезопасном соединении.

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

Если установить его разработчикам (или пользователям которые имеют доступ), браузер будет доверять сертификатам проекта и HTTPS будет работать без предупреждений безопасности.

---

# Передача сертификата разработчикам

Администратор должен передать разработчикам:

```
rootCA.crt
radmin_setup.bat
```

---

# Установка сертификата на Windows

1. Открыть `rootCA.crt`
2. Нажать **Install Certificate / Установить сертификат**
3. Выбрать **Local Machine / Локальный компьютер**
4. Указать хранилище

```
Trusted Root Certification Authorities / Доверенные корневые центры сертификации
```

5. Завершить установку

Примечание:
После установки браузер будет доверять всем сертификатам, подписанным этим Root CA.

---

# Настройка CoreDNS

Скачать CoreDNS.

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

Примечание:
CoreDNS перехватывает DNS запросы для доменов `*.yuviron.com` и возвращает IP инфраструктуры разработки.

---

# Запуск CoreDNS

```
cd C:\coredns
coredns.exe -conf Corefile
```

Примечание:
CoreDNS запускается как локальный DNS сервер.

---

# Открытие DNS порта

```
netsh advfirewall firewall add rule name="DNS TCP" dir=in action=allow protocol=TCP localport=53
```

```
netsh advfirewall firewall add rule name="DNS UDP" dir=in action=allow protocol=UDP localport=53
```

Примечание:
Открывается порт **53**, который используется DNS сервером для обработки запросов.

---

# Настройка portproxy

```
netsh interface portproxy add v4tov4 listenport=80 listenaddress=26.240.80.131 connectport=80 connectaddress=192.168.147.128
```

```
netsh interface portproxy add v4tov4 listenport=443 listenaddress=26.240.80.131 connectport=443 connectaddress=192.168.147.128
```

Примечание:
Windows принимает HTTP/HTTPS трафик и пересылает его на Ubuntu VM, где работает Docker инфраструктура.

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

Примечание:
Команда очищает DNS кеш Windows, чтобы система начала использовать новый DNS сервер.

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

Примечание:
Создаётся общая Docker сеть, через которую контейнеры могут взаимодействовать друг с другом.

---

Запустить сервисы:

```
cd /opt/yuviron-server/infra
docker compose -f compose.dev.yml up -d
```

Примечание:
Команда запускает все контейнеры dev-окружения в **фоновом режиме**.

---

Запустить edge nginx:

```
cd /opt/yuviron-server/edge
docker compose up -d
```

Примечание:
Edge nginx принимает внешние HTTPS запросы и проксирует их на внутренние сервисы.

---

# Проверка контейнеров

```
docker ps --format "{{.Names}}"
```

Примечание:
Команда показывает список запущенных контейнеров Docker.

Ожидаемый результат:

```
yuviron-edge-nginx
yuviron-dev-backend
yuviron-dev-redis
yuviron-dev-frontend
yuviron-dev-mysql
```

---

# Проверка работы приложения

```
https://dev.yuviron.com
https://api-dev.yuviron.com
```

---

# Что делает администратор

1. Устанавливает Docker
2. Настраивает env файл
3. Генерирует сертификаты
4. Настраивает CoreDNS
5. Настраивает portproxy
6. Запускает инфраструктуру
7. Передаёт разработчикам rootCA

---

# Что делает разработчик

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

# Полезные команды

Просмотр контейнеров

```
docker ps
```

Просмотр логов

```
docker logs <container>
```

Перезапуск контейнеров

```
docker compose restart
```

Остановка инфраструктуры

```
docker compose down
```

---

# Безопасность

Файл `rootCA.crt` должен распространяться **только внутри команды разработки**.

---

# Лицензия

Internal infrastructure repository.
Используется исключительно для разработки Yuviron.
