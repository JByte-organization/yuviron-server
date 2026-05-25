# Тесты

Как запускать и поддерживать тесты инфраструктурных скриптов.

[← К README](../README.md)

## Быстрый запуск

```bash
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s scripts/tests
```

Запуск одного тестового файла:

```bash
python3 -m unittest scripts/tests/test_render_nginx.py
```

## Интеграционный dry-run e2e

Тест `scripts/tests/test_stack_e2e_dry_run.py` проверяет полный CLI-цикл `init.py -> stack preflight --dry-run -> stack up --dry-run`. По умолчанию он пропускается, чтобы обычный unit-suite не требовал Docker daemon и не создавал временную Docker network.

Для запуска:

```bash
YUVIRON_RUN_DOCKER_E2E=1 python3 -m unittest scripts/tests/test_stack_e2e_dry_run.py
```

Тест собирает минимальный временный проект, создаёт fixture `env/dev.env`, запускает `init.py`, добавляет dummy TLS files для preflight-проверки путей, затем выполняет `preflight` и `up` через `docker compose --dry-run`; `up --dry-run` также проверяет migrator profile `migrate`.

## Принципы

* Тесты должны работать без настоящих секретов и локальных `env/dev.env` или `env/prod.env`.
* Если тесту нужен env-файл, он создаёт временную fixture в `tempfile.TemporaryDirectory()`.
* Для тестовой генерации runtime-конфига используйте `scripts/generate-config.py --common-env-file ... --env-file ...`.
* Эти override-флаги предназначены только для test/CI fixtures; для реального запуска нужно создать `env/<env>.env` из `env/example.env` и заполнить настоящие значения.

## Генерация конфига в тестах

Тест `scripts/tests/test_generate_config.py` создаёт временные `common.env` и `dev.env`, передаёт их в CLI и проверяет, что в `deploy.env` не попали реальные локальные dev-секреты.

```bash
python3 scripts/generate-config.py \
  --env dev \
  --domain example.com \
  --apps admin,backoffice \
  --output-dir /tmp/yuviron-generated \
  --common-env-file /tmp/common.env \
  --env-file /tmp/dev.env
```

CLI при таком запуске предупреждает, что используется временный/override ENV и для реальной работы нужно создать настоящий `env/dev.env`.

## Тестовые модули

| Файл | Что покрывает |
|---|---|
| `test_generate_config.py` | `generate-config.py` end-to-end: генерация `deploy.env` из fixture env-файлов |
| `test_render_nginx.py` | Jinja2-рендеринг `nginx.conf` из шаблонов - маршруты, TLS, rate limits, CSP, media CDN |
| `test_compose_generator.py` | Рендеринг `compose.frontends.yml` из `config/apps.yml` |
| `test_security_audit.py` | `security audit`: hardening, published ports, env safety, default secrets, git-tracked файлы |
| `test_stack_smoke.py` | Stack-команды: `cmd_up` (dry-run, skip-migrate, no-build, skip-swagger, rollback), `cmd_migrate`, `cmd_swagger_gen`, `_prepare_frontend_swagger`, `cmd_cache_purge`; утилиты `_https_route_url`, `_warn_nonstandard_public_ports`, `_media_route_host` |
| `test_smoke_logic.py` | Unit-тесты smoke: resolve smoke path, порядок маршрутов, edge cases |
| `test_doctor.py` | `doctor`: Docker/Compose, Tailscale, DNS, cert/key, порты, storage, firewall |
| `test_env_validation.py` | Env validation schema: обязательные ключи, форматы (`port`, `nginx-rate`, `cidr-list`), weak-secret policy |
| `test_preflight_generated.py` | Preflight freshness manifest: stale/missing generated files, hash-checks |
| `test_preflight_nginx.py` | Preflight nginx config validation через `nginx -t` |
| `test_core_preflight.py` | Ядро preflight: compose config, network, права, upstream services |
| `test_backup_restore_test.py` | `backup restore-test`: временный MySQL, import dump, проверка таблиц, cleanup |
| `test_backup_docker_utils.py` | Redis BGSAVE: `_redis_exec_cmd`, `_redis_lastsave`, `_trigger_redis_bgsave` — happy path, timeout, ошибки Redis |
| `test_certs_generate.py` | `certs generate`: mkcert и letsencrypt flows, SAN-список из routes.env, per-route файлы |
| `test_certs_reload.py` | `certs reload`: `nginx -t` + `nginx -s reload` внутри контейнера |
| `test_dns_generate.py` | `dns generate`: генерация Corefile, template zone, ACL, AAAA NXDOMAIN |
| `test_htpasswd.py` | Генерация и ротация htpasswd; `rotate-htpasswd` - атомарная замена файлов |
| `test_config_loader_routes.py` | Загрузка и валидация `config/routes.yml`: host, upstream, rate limits, upload |
| `test_config_loader_stack_ports.py` | Загрузка HTTP/HTTPS портов из env и их валидация |
| `test_models.py` | Generation context models: объекты маршрутов, apps, окружений |
| `test_paths.py` | Резолюция путей: `STORAGE_PATH`, `SEQ_STORAGE_PATH`, `CERT_FILE`, cert-режимы |
| `test_findings.py` | Findings/result aggregation: сбор ERROR/WARNING, exit code политика |
| `test_tools_docker_clean.py` | `tools docker-clean`: report/safe/build-cache/deep режимы, флаг `--reserved-space` |
| `test_appsettings.py` | `appsettings gen`: генерация `appsettings.json` для API и media worker — CORS origins, секреты, атомарная запись, права 600 |
| `test_backend_workflows.py` | Shared backend CI/CD workflow: вызов `appsettings gen`, передача секретов, отсутствие inline Python heredoc |
| `test_python_requirements.py` | `requirements.txt`: пакеты установлены, версии совпадают |
| `test_stack_e2e_dry_run.py` | End-to-end dry-run: `init.py -> preflight --dry-run -> up --dry-run` (требует Docker, запускается через `YUVIRON_RUN_DOCKER_E2E=1`) |