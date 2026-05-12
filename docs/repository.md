# Репозиторий, роли и безопасность

Структура репозитория, расположение runner, роли администратора и разработчика, базовые правила безопасности.

[← К README](../README.md)

## Структура репозитория

```text
yuviron-server/
├── certs/
├── config/
│   ├── apps.yml
│   ├── project.yml
│   └── routes.yml
├── docs/
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

## Расположение runner

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

## Роли

### Администратор

1. устанавливает Docker
2. настраивает `env/dev.env` и `env/prod.env`
3. настраивает сертификаты
4. создаёт сеть `yuviron_shared`
5. запускает preflight-проверку
6. запускает инфраструктуру
7. настраивает GitHub runner
8. следит за CI/CD
9. при необходимости настраивает optional CoreDNS on Windows и portproxy для RadminVPN legacy compatibility
10. настраивает Tailscale ACL и Tailscale SSH для Linux VM, если используется Tailnet-доступ
11. передаёт разработчикам `rootCA.crt`, если используется локальный Root CA

---

### Разработчик

1. подключается через Tailscale (preferred) или RadminVPN (legacy compatibility), если это требуется
2. получает доступ к dev-доменам
3. при необходимости устанавливает `rootCA.crt`
4. указывает внутренний DNS-сервер, если используется optional CoreDNS on Windows для RadminVPN legacy-схемы или Tailnet Split DNS
5. очищает DNS-кеш
6. проверяет локальную сборку frontend перед push
7. использует dev-домены для тестирования

Разработчикам не нужно удалять RadminVPN, если он уже используется. Для новых подключений предпочтительнее Tailscale, потому что он проще для onboarding, работает с мобильными устройствами и не требует ручного route management. CoreDNS on Windows нужен только как optional local DNS endpoint, а не как обязательный сетевой слой.

Пример dev-доменов:

```text
https://dev.yuviron.com
https://dev-backoffice.yuviron.com
https://dev-admin.yuviron.com
https://dev-api.yuviron.com
```

---

## Безопасность

* сертификаты и ключи должны распространяться только внутри команды
* `prod.env` не должен публиковаться
* runner не должен запускаться от `root`
* доступ к Docker должен быть ограничен доверенными пользователями
* `rootCA.crt` должен распространяться только среди участников команды разработки
* dev-доступ через VPN/Tailnet и внутренний DNS предпочтительнее, если среда не должна быть общедоступной
* RadminVPN сохраняется как legacy compatibility; новые подключения лучше заводить через Tailscale
* CoreDNS on Windows используется только как optional local DNS endpoint для dev-доменов
* Tailscale ACL должен ограничивать SSH-доступ только владельцем инфраструктуры
* на Linux VM нужно держать `--accept-dns=false`, если MagicDNS ломает внешний DNS resolution

---

## Лицензия

Internal infrastructure repository.
Используется исключительно для разработки и эксплуатации Yuviron.
