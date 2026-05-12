# Подготовка сервера

Требования к серверу, создание VM, клонирование репозитория и установка Docker.

[← К README](../README.md)

## Требования к серверу

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

## Подготовка сервера

### Создание виртуальной машины

Создать сервер на Ubuntu 22.04+ и подключиться к нему по SSH.

Если используется dev-инфраструктура с внутренней сетевой схемой, необходимо заранее понимать:

* IP Ubuntu VM
* IP Windows host
* Tailnet IP Linux VM для preferred Tailscale-доступа
* RadminVPN IP / адрес для DNS только если нужна legacy compatibility
* схему проброса портов 80/443, если Windows host остаётся gateway для legacy-схемы
* Tailnet IP для `dev-vm` и `host-pc`, если используется Tailscale
* какой access layer используется для конкретного разработчика: Tailscale preferred или RadminVPN legacy compatibility
* нужен ли CoreDNS on Windows как optional local DNS endpoint

---

### Клонирование репозитория

```bash
cd /opt
git clone https://github.com/JByte-organization/yuviron-server
cd yuviron-server
```

Каталог `/opt` используется для размещения сторонних сервисов и инфраструктурных проектов.

---

## Python-зависимости

Инфраструктурные CLI-скрипты используют Python-зависимости из `requirements.txt`:

```bash
cd /opt/yuviron-server
python3 -m pip install -r requirements.txt
```

Минимально требуются `PyYAML` для YAML-конфигурации и `Jinja2` для nginx templates.

---

## Установка Docker

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
