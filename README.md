# Yuviron Server Infrastructure

Инфраструктурный репозиторий для развёртывания **dev** и **prod** окружений Yuviron и автоматизации CI/CD через GitHub Actions.

Коротко о текущей схеме:

* единый `infra/compose.yml` для dev/prod;
* основной интерфейс управления — `scripts/cli.py`;
* source of truth хранится в `config/` и `env/`;
* runtime-файлы генерируются в `generated/<env>/`;
* `env/example.env` и `env/common.env` трекаются как шаблоны;
* бэкапы, сертификаты, preflight, smoke и cleanup доступны через CLI.

## Быстрый старт

### Dev

```bash
python3 -m pip install -r requirements.txt

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

Сетевой доступ к dev-среде: **Tailscale** — preferred private access layer, **RadminVPN** — legacy compatibility, **CoreDNS on Windows** — optional local DNS endpoint. Подробнее: [docs/networking.md](docs/networking.md).

### Prod

```bash
python3 -m pip install -r requirements.txt

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
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s scripts/tests
```

Тесты должны работать без настоящих секретов и без локальных `env/dev.env` / `env/prod.env`. Подробнее: [docs/tests.md](docs/tests.md).

## Документация

* [Индекс документации](docs/index.md)
* [Обзор и архитектура](docs/overview.md)
* [Подробная архитектура](docs/architecture.md)
* [CLI и команды](docs/commands.md)
* [Operations и запуск стека](docs/operations.md)
* [Runtime-конфигурация](docs/runtime-config.md)
* [Подготовка сервера](docs/server-setup.md)
* [Бэкапы](docs/backups.md)
* [Сертификаты](docs/certificates.md)
* [Dev-доступ и сети](docs/networking.md)
* [CI/CD и self-hosted runner](docs/cicd.md)
* [Тесты](docs/tests.md)
* [Репозиторий, роли и безопасность](docs/repository.md)

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
