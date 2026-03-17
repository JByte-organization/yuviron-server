# Локальная разработка Yuviron

Этот раздел описывает полный процесс подготовки и запуска **dev-окружения проекта Yuviron**.

Документ включает:

1. Установку Docker на сервере
2. Генерацию SSL сертификатов для разработки
3. Настройку локального DNS внутри сети RadminVPN
4. Настройку сетевой маршрутизации
5. Запуск dev-окружения проекта

После настройки разработчики смогут открывать проект по доменам:

```
https://dev.yuviron.com
https://api-dev.yuviron.com
```

---

# 1. Архитектура сети разработки

Схема работы инфраструктуры выглядит следующим образом:

```
Разработчик (RadminVPN)
        ↓
DNS запрос (*.yuviron.com)
        ↓
CoreDNS (Windows хост)
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

# 2. Подготовка сервера Ubuntu

Перед запуском проекта необходимо подготовить сервер:

1. Установить Docker
2. Сгенерировать SSL сертификаты

Эти действия выполняются на **Ubuntu VM**, где размещён проект.

---

# 3. Установка Docker

Для установки Docker используется скрипт:

```
/opt/yuviron-server/scripts/docker_install.sh
```

Скрипт выполняет:

1. Добавление официального Docker GPG ключа
2. Добавление Docker repository
3. Установку Docker Engine
4. Установку Docker Compose plugin
5. Добавление пользователя в группу docker

Это позволяет запускать Docker **без sudo**.

---

## 3.1 Запуск установки Docker

Перейти в папку scripts:

```
cd /opt/yuviron-server/scripts
```

Сделать скрипт исполняемым:

```
chmod +x docker_install.sh
```

Запустить установку:

```
./docker_install.sh
```

---

## 3.2 Важно: указать своего пользователя

В конце скрипта есть строка:

```
sudo usermod -aG docker nf
```

`nf` — это имя пользователя на сервере.

Его необходимо заменить на своего пользователя.

Пример:

```
sudo usermod -aG docker nikolay
```

После выполнения команды необходимо **перезайти в систему**, чтобы группа docker начала работать.

---

# 4. Генерация SSL сертификатов

Для HTTPS в dev-окружении используется **mkcert**.

mkcert — это инструмент, который создаёт локальные SSL сертификаты и собственный Root CA.

Это позволяет использовать HTTPS без предупреждений браузера.

---

## 4.1 Скрипт генерации сертификатов

Файл:

```
/opt/yuviron-server/scripts/regen-yuviron-certs.sh
```

Скрипт выполняет:

1. Проверку установлен ли mkcert
2. Установку mkcert если он отсутствует
3. Создание локального Root CA
4. Генерацию SSL сертификатов для доменов
5. Копирование сертификатов в проект

---

## 4.2 Домены для которых генерируются сертификаты

```
yuviron.com
api.yuviron.com
dev.yuviron.com
api-dev.yuviron.com
*.yuviron.com
```

---

## 4.3 Где сохраняются сертификаты

Сертификаты сохраняются в папку:

```
/opt/yuviron-server/certs
```

Файлы:

```
yuviron-cert.pem
yuviron-key.pem
```

Эти сертификаты используются **edge nginx**.

---

## 4.4 Запуск генерации сертификатов

Перейти в папку scripts:

```
cd /opt/yuviron-server/scripts
```

Сделать скрипт исполняемым:

```
chmod +x regen-yuviron-certs.sh
```

Запустить:

```
./regen-yuviron-certs.sh
```

После выполнения появится файл:

```
~/rootCA.crt
```

---

# 5. Для чего нужен rootCA.crt

Этот файл — **локальный центр сертификации (Root CA)**.

Если установить его разработчикам, браузер будет доверять сертификатам проекта.

Это позволит открывать:

```
https://dev.yuviron.com
```

без предупреждений безопасности.

---

# 6. Передача сертификата разработчикам

Файл:

```
rootCA.crt
```

необходимо передать разработчикам.

Например через:

* SCP
* Telegram
* Git
* Shared folder

---

# 7. Установка сертификата на Windows (RU)

Разработчик должен установить файл:

```
rootCA.crt
```

### Шаг 1

Открыть файл.

### Шаг 2

Нажать:

```
Install Certificate
```

### Шаг 3

Выбрать:

```
Local Machine
```

### Шаг 4

Выбрать хранилище сертификатов:

```
Trusted Root Certification Authorities
```

### Шаг 5

Завершить установку.

После этого браузер будет доверять сертификатам проекта.

---


# 8. Настройка локального DNS

Для резолвинга доменов внутри сети **RadminVPN** используется **CoreDNS**, который запускается на Windows-хосте.

Он отвечает за:

```
yuviron.com
*.yuviron.com
```

и направляет их на сервер разработки.

---

# 9. Установка CoreDNS

## Скачать CoreDNS

Скачать релиз:

```
https://github.com/coredns/coredns/releases/tag/v1.14.2
```

Файл:

```
coredns_1.14.2_windows_amd64.tgz
```

---

## Распаковка

Создать папку:

```
C:\coredns
```

Распаковать архив.

В результате должен появиться файл:

```
C:\coredns\coredns.exe
```

---

## Создать конфигурацию CoreDNS

Создать файл:

```
C:\coredns\Corefile
```

Содержимое:

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

Этот конфиг делает:

```
yuviron.com → 26.240.80.131
*.yuviron.com → 26.240.80.131
```

---

## Запуск CoreDNS

```
cd C:\coredns
coredns.exe -conf Corefile
```

CoreDNS начинает слушать порт:

```
53
```

---

# 10. Открыть DNS порт в Windows Firewall

Запустить CMD от администратора:

```
netsh advfirewall firewall add rule name="DNS TCP" dir=in action=allow protocol=TCP localport=53
netsh advfirewall firewall add rule name="DNS UDP" dir=in action=allow protocol=UDP localport=53
```

---

# 11. Настройка portproxy

Windows должен пробрасывать HTTP и HTTPS трафик на VM.

```
netsh interface portproxy add v4tov4 listenport=80 listenaddress=26.240.80.131 connectport=80 connectaddress=192.168.147.128
netsh interface portproxy add v4tov4 listenport=443 listenaddress=26.240.80.131 connectport=443 connectaddress=192.168.147.128
```

---

# 12. Настройка DNS у разработчиков

Каждый разработчик должен указать DNS сервер сети RadminVPN.

### Открыть

```
Network Connections
```

### Найти адаптер

```
Radmin VPN
```

### Открыть

```
Properties → IPv4
```

### Указать DNS

```
Preferred DNS server: 26.240.80.131
Alternate DNS server: 8.8.8.8
```

### Очистить DNS кеш

```
ipconfig /flushdns
```

---

# 13. Проверка DNS

```
nslookup dev.yuviron.com
```

Ожидаемый результат:

```
Address: 26.240.80.131
```

---

# 14. Запуск проекта

## Создать docker сеть

```
docker network create yuviron_shared
```

---

## Запустить инфраструктуру

```
cd /opt/yuviron-server/infra
docker compose -f compose.dev.yml up -d
```

Docker поднимет:

```
yuviron-dev-mysql
yuviron-dev-redis
yuviron-dev-migrator
yuviron-dev-backend
yuviron-dev-frontend
yuviron-dev-nginx
```

---

## Запустить edge nginx

```
cd /opt/yuviron-server/edge
docker compose up -d
```

Будет поднят контейнер:

```
yuviron-edge-nginx
```

---

# 15. Проверка контейнеров

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

# 16. Проверка работы приложения

После запуска проект должен открываться:

```
https://dev.yuviron.com
https://api-dev.yuviron.com
```

API endpoints:

```
https://api-dev.yuviron.com/swagger/
https://api-dev.yuviron.com/health/
```

---

# 17. Что должен сделать администратор

Администратор должен:

1. Установить Docker
2. Сгенерировать сертификаты
3. Поднять CoreDNS
4. Настроить portproxy
5. Запустить проект
6. Подключить разработчиков к сети RadminVPN

---

# 18. Что должен сделать разработчик

Разработчик должен:

1. Подключиться к **RadminVPN**
2. Указать DNS

```
26.240.80.131
```

3. Очистить DNS кеш

```
ipconfig /flushdns
```

4. Открыть в браузере

```
https://dev.yuviron.com
```

После этого можно работать с dev-версией проекта.
