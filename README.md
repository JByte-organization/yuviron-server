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
cp env/example.env env/dev.env
# Отредактируйте env/dev.env и заполните реальные значения.

python3 scripts/init.py --env dev --domain yuviron.com --no-up
./scripts/cli.py stack preflight dev
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
```

### Prod

```bash
cp env/example.env env/prod.env
# Отредактируйте env/prod.env и заполните production-значения.

python3 scripts/init.py --env prod --domain yuviron.com --no-up
./scripts/cli.py stack preflight prod
./scripts/cli.py stack up prod
./scripts/cli.py stack smoke prod
```

Типичные prod-хосты:

```text
https://yuviron.com
https://backoffice.yuviron.com
https://admin.yuviron.com
https://api.yuviron.com
```

## Частые команды

```bash
./scripts/cli.py stack preflight dev
./scripts/cli.py stack up dev
./scripts/cli.py stack smoke dev
./scripts/cli.py backup create
./scripts/cli.py backup verify
./scripts/cli.py security audit dev
./scripts/cli.py tools docker-clean --mode report
```

Полный справочник команд: [docs/commands.md](docs/commands.md).

## Тесты

```bash
python3 -m unittest discover -s scripts/tests
```

Тесты должны работать без настоящих секретов и без локальных `env/dev.env` / `env/prod.env`. Подробнее: [docs/tests.md](docs/tests.md).

## Документация

* [Индекс документации](docs/index.md)
* [Обзор и архитектура](docs/overview.md)
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
* Используйте `backup verify`, а не только `backup create`.
* Не запускайте CLI от root без необходимости.

## Лицензия

Internal infrastructure repository. Используется исключительно для разработки и эксплуатации Yuviron.
