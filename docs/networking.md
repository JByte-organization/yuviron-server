# Dev-доступ и сети

Preferred-доступ через Tailscale, legacy compatibility через RadminVPN, optional CoreDNS on Windows, Tailnet ACL, Tailscale DNS и Docker network.

[← К README](../README.md)

## Dev-доступ для разработчиков

Dev-инфраструктура разделяет приватный access layer и локальный DNS endpoint. Короткая архитектурная маркировка:

* **RadminVPN** - legacy compatibility для существующих рабочих мест, старых маршрутов и `radmin_setup.bat`.
* **Tailscale** - preferred private access layer для новых устройств, SSH и прямого доступа к Linux VM через Tailnet.
* **CoreDNS on Windows** - optional local DNS endpoint для dev-доменов, если нужен резолвинг `*.yuviron.com` через Windows host.

Это не три обязательных слоя, которые нужно включать одновременно для каждого разработчика. Для новых подключений базовым выбором считается Tailscale. RadminVPN сохраняется, чтобы не ломать существующие рабочие места, а CoreDNS на Windows включается только там, где нужен локальный DNS endpoint для legacy RadminVPN-схемы или Tailnet Split DNS.

Архитектурные границы:

* CoreDNS не является отдельным access layer; это optional DNS endpoint на Windows host.
* RadminVPN остаётся только legacy compatibility path.
* Tailscale работает как preferred private access layer для Linux VM.
* Tailscale daemon запускается изолированно внутри Linux VM.
* Tailscale не устанавливается на Windows host как основной VPN-слой проекта.
* Tailscale networking изолирован на уровне VM-инфраструктуры.
* Входной трафик к приложениям по Tailnet направляется напрямую на `dev-vm`.

Legacy compatibility-схема через RadminVPN и Windows CoreDNS:

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

Preferred-схема через Tailscale:

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

Tailscale-режим особенно полезен, если:

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

Если dev окружение будет доступно по другой схеме, блоки CoreDNS / portproxy / Tailnet ACL можно адаптировать под конкретную сеть. CoreDNS и portproxy нужны только для схем, где Windows host выступает DNS/traffic gateway.

---

## Настройка CoreDNS на Windows (optional)

**CoreDNS on Windows** - optional local DNS endpoint, а не обязательная часть private access layer. Используй его, если dev-среда работает через Windows host с отдельным DNS, например для RadminVPN legacy compatibility или Tailnet Split DNS.

Corefile не нужно редактировать руками: он должен генерироваться из `generated/<env>/routes.env`, чтобы DNS всегда совпадал с nginx routes.

Создать директорию:

```text
C:\coredns
```

Распаковать туда `coredns.exe`.

Сгенерировать Corefile из runtime routes проекта:

```bash
./scripts/cli.py dns generate --env dev --domain yuviron.com --ip 100.81.228.68
```

Результат будет сохранён в:

```text
generated/dev/Corefile
```

После генерации файл можно перенести или синхронизировать в `C:\coredns\Corefile` на Windows host.

Сгенерированная конфигурация:

* берёт актуальный список hosts из `generated/dev/routes.env`
* через `template IN A` отправляет `yuviron.com` и все subdomains на IP из `--ip`
* явно отвечает `NXDOMAIN` на `AAAA` для этой зоны, чтобы IPv6-запросы не проваливались дальше по chain
* ограничивает fallback recursive DNS через `acl`: по умолчанию разрешён только Tailnet CIDR `100.64.0.0/10`, остальные клиенты не могут использовать CoreDNS как публичный рекурсор
* все остальные разрешённые DNS-запросы форвардятся на upstream public resolvers `1.1.1.1` и `8.8.8.8`

Если CoreDNS должен обслуживать не Tailscale, а другую приватную сеть, укажи разрешённые сети явно. Флаг можно повторять или передавать через запятую:

```bash
./scripts/cli.py dns generate --env dev --domain yuviron.com --ip 100.81.228.68 --acl-net 100.64.0.0/10 --acl-net 10.8.0.0/24
```

После изменения `config/routes.yml`, `config/apps.yml`, набора apps или base domain сначала перегенерируй runtime config, затем Corefile:

```bash
python3 scripts/init.py --env dev --domain yuviron.com --no-up
./scripts/cli.py dns generate --env dev --domain yuviron.com --ip 100.81.228.68
```

---

## Запуск CoreDNS

```powershell
cd C:\coredns
coredns.exe -conf Corefile
```

Для production такая схема обычно не используется. Для dev внутри приватной сети это нормальный вариант, если Windows host действительно нужен как local DNS endpoint.

---

## Windows Firewall: DNS для optional CoreDNS endpoint

Если CoreDNS запускается на Windows host machine и обслуживает internal DNS для dev-доменов, Windows Firewall должен разрешать входящий DNS-трафик на порт `53`. Это требуется только когда Windows host используется как DNS endpoint для RadminVPN legacy-схемы или Tailnet Split DNS.

Нужно открыть оба протокола:

* **UDP 53** - стандартные DNS-запросы
* **TCP 53** - большие DNS-ответы и fallback resolution

PowerShell:

```powershell
New-NetFirewallRule `
  -DisplayName "CoreDNS UDP 53 Internal" `
  -Direction Inbound `
  -Action Allow `
  -Protocol UDP `
  -LocalPort 53
```

```powershell
New-NetFirewallRule `
  -DisplayName "CoreDNS TCP 53 Internal" `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalPort 53
```

Без этих firewall rules, если Windows host используется как DNS endpoint:

* internal DNS resolution внутри Tailnet или legacy RadminVPN может не работать
* `yuviron.com` / `dev.yuviron.com` могут перестать резолвиться
* мобильные устройства могут потерять доступ к internal domains
* Split DNS functionality может сломаться

---

## Настройка portproxy для legacy RadminVPN

Если Windows host принимает трафик из RadminVPN legacy-схемы и перенаправляет его на Ubuntu VM, нужно настроить `portproxy`.

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

## Настройка DNS у разработчиков для legacy/CoreDNS

Если dev доступен через legacy RadminVPN и optional CoreDNS on Windows, разработчику нужно указать внутренний DNS-сервер:

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
nslookup dev-i.yuviron.com
```

Если всё настроено правильно, домены должны резолвиться в нужный IP. Для новых Tailscale-подключений этот шаг обычно не нужен, если только Windows CoreDNS не используется как optional DNS endpoint внутри Tailnet.

---

## Tailscale как preferred private access layer

Tailscale используется как preferred private access layer для dev-инфраструктуры. Он не требует удалять RadminVPN там, где старая схема уже работает, но для новых устройств и новых сценариев доступа именно Tailscale считается основным путём к Linux VM через Tailnet.

В текущей схеме:

* RadminVPN остаётся доступен для legacy compatibility.
* Tailscale является предпочтительным способом подключения для новых устройств.
* Мобильные устройства могут обращаться к dev-проекту напрямую через Tailnet.
* Не нужно поддерживать отдельные `.bat` route scripts для доступа к VM.
* Не нужно вручную перенастраивать маршруты RadminVPN для каждого нового сценария.
* CoreDNS может оставаться на Windows host как optional local DNS endpoint.
* Tailscale daemon работает внутри Linux VM.
* Tailscale networking ограничен инфраструктурным слоем Linux VM.

Важно: Tailscale нужно описывать как preferred private access layer, а не как принудительный replacement, который ломает legacy RadminVPN-схему. RadminVPN остаётся compatibility path, CoreDNS on Windows остаётся optional DNS endpoint.

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

В шаблоне ниже есть доступ к `host-pc:53`. Это правило нужно только если **CoreDNS on Windows** используется как optional local DNS endpoint. Если Windows host не обслуживает DNS для Tailnet, правило `host-pc:53` можно убрать.

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

* участники Tailnet могут обращаться к DNS на Windows host: `host-pc:53`, если включён optional CoreDNS endpoint
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

Windows host не должен рассматриваться как основной Tailscale VPN layer проекта. Он продолжает выполнять свои инфраструктурные обязанности, включая optional CoreDNS и standard OpenSSH Server, а основной приватный application access через Tailscale направляется на Linux VM.

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
