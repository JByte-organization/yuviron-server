# Dev-доступ и сети

RadminVPN, CoreDNS, portproxy, Tailscale, Tailnet ACL, Tailscale DNS и Docker network.

[← К README](../README.md)

## Dev-доступ для разработчиков

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

## Настройка CoreDNS для dev-окружения

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

## Запуск CoreDNS

```powershell
cd C:\coredns
coredns.exe -conf Corefile
```

Для production такая схема обычно не используется, но для dev внутри VPN — это нормальный вариант.

---

## Windows Firewall: DNS для CoreDNS / Tailscale

CoreDNS запускается на Windows host machine и обслуживает internal DNS для dev-доменов. Если Windows host используется как DNS endpoint внутри Tailnet, Windows Firewall должен разрешать входящий DNS-трафик на порт `53`.

Нужно открыть оба протокола:

* **UDP 53** — стандартные DNS-запросы
* **TCP 53** — большие DNS-ответы и fallback resolution

PowerShell:

```powershell
New-NetFirewallRule `
  -DisplayName "CoreDNS UDP 53 Tailscale" `
  -Direction Inbound `
  -Action Allow `
  -Protocol UDP `
  -LocalPort 53
```

```powershell
New-NetFirewallRule `
  -DisplayName "CoreDNS TCP 53 Tailscale" `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalPort 53
```

Без этих firewall rules:

* internal DNS resolution внутри Tailnet может не работать
* `yuviron.com` / `dev.yuviron.com` могут перестать резолвиться
* мобильные устройства могут потерять доступ к internal domains
* Split DNS functionality может сломаться

---

## Настройка portproxy

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

## Настройка DNS у разработчиков

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

## Tailscale как приватный слой доступа

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

## Установка Tailscale на Linux VM

Tailscale устанавливается внутри Linux VM, а не на Windows host как основной VPN-слой проекта. Инструкция ниже подходит для Ubuntu Server / Debian-based VM.

### Установка

На Linux VM:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
```

Скрипт устанавливает:

* `tailscaled` daemon
* `tailscale` CLI
* systemd service

Включить и запустить daemon:

```bash
sudo systemctl enable --now tailscaled
```

Проверить сервис:

```bash
systemctl status tailscaled
```

### Авторизация VM в Tailnet

Для этой инфраструктуры рекомендуется сразу включать Tailscale SSH и отключать автоматическую замену системного DNS resolver:

```bash
sudo tailscale up --accept-dns=false --ssh
```

Команда выдаст login URL:

```text
https://login.tailscale.com/...
```

Открой ссылку на доверенном устройстве, войди в Tailscale account и добавь VM в Tailnet. После авторизации VM появится в списке устройств.

Проверить Tailnet IP:

```bash
tailscale ip
```

Ожидаемый формат:

```text
100.x.x.x
```

Проверить состояние Tailnet:

```bash
tailscale status
```

### SSH-подключение

С другого устройства, где установлен и включён Tailscale:

```bash
ssh <LINUX_VM_USER>@<DEV_VM_TAILNET_IP>
```

Если MagicDNS доступен на клиентском устройстве, можно подключаться по имени узла:

```bash
ssh <LINUX_VM_USER>@dev-vm
```

Tailscale SSH управляется ACL policy. Для этой схемы доступ должен быть ограничен владельцем инфраструктуры и локальным пользователем `<LINUX_VM_USER>` на Linux VM.

### Мобильный доступ

Для мобильного доступа:

1. установить Tailscale на телефон
2. войти в тот же Tailnet
3. включить VPN
4. подключаться к VM через SSH-клиент

Подходящие клиенты:

* Termius
* JuiceSSH
* Blink Shell

Схема подключения:

```text
Phone / Laptop
      ↓
Tailscale mesh
      ↓
Linux VM / dev-vm
      ↓
Docker containers
      ↓
edge nginx / backend / frontend apps
```

### Docker и сервисы внутри VM

Tailscale работает на уровне network stack Linux VM. Это означает, что Tailnet-клиент может обращаться к сервисам на VM, если одновременно выполняются условия:

* сервис опубликован на host-порт VM
* сервис слушает `0.0.0.0` или VM interface
* Linux firewall разрешает входящий трафик
* Tailscale ACL разрешает нужный порт

Для Yuviron это в первую очередь относится к `edge nginx` и dev HTTPS/HTTP портам. Базы данных, брокеры и внутренние сервисы не нужно открывать в Tailnet без отдельной причины.

### SSH наружу

После проверки доступа через Tailnet можно закрыть внешний SSH-доступ на Linux VM. Это нужно делать только если есть рабочий Tailscale SSH, консольный доступ к VM или другой recovery path.

Пример для `ufw`:

```bash
sudo ufw deny 22/tcp
```

Более безопасный вариант для dev-инфраструктуры:

* оставить SSH доступным через Tailnet
* закрыть публичный `22/tcp` во внешнем firewall
* управлять доступом через Tailscale ACL

### Несколько VM

Если позже появятся дополнительные VM:

```text
dev-vm
prod-vm
media-worker
nas
```

Tailnet позволит работать с ними как с приватной инфраструктурной сетью:

```bash
ssh <LINUX_VM_USER>@dev-vm
ssh <LINUX_VM_USER>@prod-vm
```

Для production-узлов ACL должны быть строже, чем для dev, и не должны автоматически копироваться из dev policy.

---

## Полезные команды Tailscale

Статус Tailnet:

```bash
tailscale status
```

Tailnet IP текущей VM:

```bash
tailscale ip
```

Проверить текущую конфигурацию:

```bash
tailscale debug prefs
```

Перезапустить daemon:

```bash
sudo systemctl restart tailscaled
```

Повторно применить рекомендуемую конфигурацию VM:

```bash
sudo tailscale up --accept-dns=false --ssh
```

Выйти из Tailnet:

```bash
sudo tailscale logout
```

Проверить логи daemon:

```bash
journalctl -u tailscaled -n 100 --no-pager
```

Технические преимущества Tailscale для этой инфраструктуры:

* WireGuard-based transport
* NAT traversal
* mesh networking
* ACL-based access control
* Tailscale SSH integration
* optional MagicDNS / Split DNS
* subnet routing и exit nodes, если они явно включены
* Zero Trust access model

В отличие от legacy RadminVPN-схемы, Tailscale лучше подходит для управляемого infrastructure access: ACL, SSH policy, мобильные клиенты, понятная модель устройств и меньше ручного route management.

---

## Tailscale DNS / MagicDNS Issue

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

## DNS Resolution Fix

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

## Tailscale ACL policy

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

## SSH через Tailnet

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

## Docker network

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
