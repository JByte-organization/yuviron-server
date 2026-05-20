# Yuviron Server Infrastructure

Инфраструктурный репозиторий для развёртывания **dev** и **prod** окружений Yuviron и автоматизации CI/CD через GitHub Actions.

Коротко о текущей схеме:

* единый `infra/compose.yml` для dev/prod;
* основной интерфейс управления - `scripts/cli.py`;
* source of truth хранится в `config/` и `env/`;
* runtime-файлы генерируются в `generated/<env>/`;
* `env/example.env` и `env/common.env` трекаются как шаблоны;
* бэкапы, сертификаты, preflight, smoke и cleanup доступны через CLI.

## Быстрый старт

### Dev

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp env/example.env env/dev.env
# Отредактируйте env/dev.env и заполните реальные значения.

python3 scripts/init.py --env dev --domain yuviron.com --no-up
./scripts/cli.py stack preflight dev
./scripts/cli.py doctor dev
./scripts/cli.py dns generate --env dev --domain yuviron.com --ip 100.81.228.68
./scripts/cli.py stack up dev
./scripts/cli.py stack smoke dev
```

Типичные dev-хосты после настройки DNS/routes:

```text
https://dev.yuviron.com
https://dev-backoffice.yuviron.com
https://dev-admin.yuviron.com
https://dev-aspire.yuviron.com/
https://dev-seq.yuviron.com/
https://dev-api.yuviron.com
https://dev-i.yuviron.com
```

Сетевой доступ к dev-среде: **Tailscale** - preferred private access layer, **RadminVPN** - legacy compatibility, **CoreDNS on Windows** - optional local DNS endpoint. Подробнее: [docs/networking.md](docs/networking.md).

### Prod

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp env/example.env env/prod.env
# Отредактируйте env/prod.env и заполните production-значения.

python3 scripts/init.py --env prod --domain yuviron.com --no-up
./scripts/cli.py stack preflight prod
ALLOW_PRODUCTION_MIGRATE=true ./scripts/cli.py stack up prod
./scripts/cli.py stack smoke prod
```

Типичные prod-хосты:

```text
https://yuviron.com
https://backoffice.yuviron.com
https://admin.yuviron.com
https://api.yuviron.com
https://i.yuviron.com
```

## Частые команды

```bash
./scripts/cli.py stack preflight dev
./scripts/cli.py stack up dev
./scripts/cli.py stack smoke dev
./scripts/cli.py backup create
./scripts/cli.py backup verify
./scripts/cli.py backup verify --full
./scripts/cli.py backup restore-test dev
./scripts/cli.py security audit dev
./scripts/cli.py tools docker-clean --mode report
```

Полный справочник команд: [docs/commands.md](docs/commands.md).

## Тесты

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 -m unittest discover -s scripts/tests
```

Тесты должны работать без настоящих секретов и без локальных `env/dev.env` / `env/prod.env`. Подробнее: [docs/tests.md](docs/tests.md).

## Документация

### С чего начать

* [Первый запуск](docs/getting-started.md) - от клонирования до работающего стека
* [Диагностика](docs/troubleshooting.md) - что делать, если что-то пошло не так

### Эксплуатация

* [Operations и запуск стека](docs/operations.md) - deploy, restart, smoke, hard reset
* [CLI и команды](docs/commands.md) - полный справочник команд
* [Ротация паролей](docs/passwords.md) - nginx Basic Auth, Seq, Aspire, MySQL, RabbitMQ
* [Бэкапы](docs/backups.md) - резервное копирование и восстановление
* [Сертификаты](docs/certificates.md) - mkcert, Let's Encrypt, reload

### Конфигурация и архитектура

* [Переменные окружения и nginx](docs/env.md) - env-настройки, nginx, TLS, маршруты, сборка
* [Архитектура](docs/architecture.md) - рабочая схема, компоненты, security boundaries

### Инфраструктура

* [Подготовка сервера](docs/getting-started.md) - требования, Docker, клонирование
* [Dev-доступ и сети](docs/networking.md) - Tailscale, RadminVPN, CoreDNS, portproxy
* [CI/CD и self-hosted runner](docs/cicd.md) - GitHub Actions, установка runner

### Разработка

* [Тесты](docs/tests.md) - запуск тестов, принципы, dry-run e2e
* [Репозиторий, роли и безопасность](docs/repository.md) - структура, роли, правила

## Структура

```text
yuviron-server/
├── config/
├── docs/
├── env/
├── generated/
├── infra/
├── scripts/
├── shared/
├── src/
├── storage/
└── README.md
```

## Важно

* Всегда запускайте `preflight` перед `up`.
* Проверяйте env перед запуском prod.
* Делайте backup перед обновлениями.
* `backup create` автоматически проверяет MySQL-дампы через restore-test; периодически запускайте `backup verify --full` для полного сценария.
* Не запускайте CLI от root без необходимости.

## Лицензия / License

This repository is proprietary and internal to Yuviron.

No permission is granted to use, copy, modify, distribute, sublicense,
or deploy this repository or any part of it outside Yuviron development
and operations without explicit written permission from the owner.
