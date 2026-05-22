# CI/CD и self-hosted runner

GitHub Actions, self-hosted runner, workflow, установка runner и права на директории.

[← К README](../README.md)

## Архитектура CI/CD

Для автоматизации сборки и деплоя используется **GitHub Actions с self-hosted runner**.

Runner установлен на сервере и выполняет workflow напрямую внутри инфраструктуры.

Схема работы CI/CD:

```text
GitHub Repository
        ↓
GitHub Actions Workflow
        ↓
Self-Hosted Runner
        ↓
Preflight / Build / Deploy
        ↓
Обновление dev или prod окружения
```

Runner расположен на сервере:

```text
/opt/actions-runner
```

Все workflow внутри организации **JByte-organization** могут использовать этот runner.

Актуальные shared workflow:

```text
shared/backend/deploy.yml
shared/frontend/.github/workflows/deploy.yml
```

Текущий deploy flow:

1. синхронизировать source repo в `src/yuviron-backend` или `src/yuviron-frontend`
2. выполнить preflight
3. собрать/поднять стек через `./scripts/cli.py stack up <env>`; команда перед основным `up` явно запускает EF Core migrator через compose profile `migrate`
4. просканировать собранные образы на CVE через Trivy (после build, до smoke)
5. выполнить `./scripts/cli.py stack smoke <env>`
6. показать compose status и хвосты логов
7. после успешного deploy подрезать Docker build cache через `tools docker-clean`

---

## Механизм rollback

Rollback работает на двух уровнях, которые дополняют друг друга.

**Уровень 1 — image-level rollback (внутри `stack up`, автоматически):**

Перед `docker compose up --build` CLI тегирует текущие образы всех built-сервисов как `:rollback`. Если сборка или запуск падают, CLI:

1. останавливает compose-проект
2. возвращает тег `:rollback -> :latest` для каждого сервиса
3. поднимает контейнеры без `--build` (на старых образах)
4. выбрасывает ошибку — stack-шаг в CI завершается с exit code 1

После этого стек уже работает на образах предыдущей версии.

**Уровень 2 — git-level rollback (CI-шаг `Rollback * stack`, при failure):**

CI-шаг фиксирует SHA перед деплоем и при любой ошибке в основной цепочке:

1. сбрасывает git-репозиторий источника (`backend` или `frontend`) на зафиксированный SHA
2. восстанавливает конфигурационные файлы (appsettings для backend)
3. вызывает `stack up --no-rollback --skip-migrate --no-build`
   — `--no-build`: не пересобирает образы (image-level rollback их уже восстановил); пропускает регенерацию Swagger
   — `--no-rollback`: не делает новый снэпшот (нечего сохранять)
   — `--skip-migrate`: мигратор уже отработал до сбоя; повторный запуск — no-op, но пропуск безопаснее и быстрее
4. запускает smoke test для подтверждения рабочего состояния

Комбинация гарантирует: даже если сборка сломана и не может быть восстановлена через `--build`, CI-rollback успешно завершается, опираясь на уже работающие образы предыдущей версии.

**Ограничение:** если образов предыдущей версии не существует (первый деплой или они были удалены), image-level rollback не выполняется. В этом случае CI-rollback с `--no-build` также не сможет поднять стек — необходимо ручное вмешательство.

---

## Сканирование образов (Trivy)

Vulnerability scanning работает на четырёх уровнях.

**CI `image-scan` (`ubuntu-latest`, каждый push/PR):** `aquasecurity/trivy-action` сканирует base images `mcr.microsoft.com/dotnet/aspnet:9.0` и `nginx:alpine` в **информационном режиме** (`--exit-code 0`) — они upstream и мы их не контролируем, статус CVE может меняться без каких-либо действий с нашей стороны. Затем собирает и сканирует `yuviron-edge` (edge nginx с кастомным конфигом) уже в **блокирующем режиме** (`--exit-code 1`): fixable CRITICAL/HIGH CVE в нашем built-образе ломают CI. Настройка: `--severity CRITICAL,HIGH --ignore-unfixed`; `--ignore-unfixed` убирает шум от CVE, для которых нет доступного патча.

**CI `image-scan-built` (`[self-hosted, yuviron]`, каждый push/PR):** собирает и сканирует финальные образы `yuviron-backend` и `yuviron-media-worker`. Требует наличия backend source в `src/yuviron-backend/`; если исходники не найдены — шаг пропускается с notice, не блокируя CI.

**Deploy dev (информационно):** после `stack up` Trivy (`aquasec/trivy` Docker-образ, докер-сокет уже есть на runner) сканирует реально собранные образы `yuviron-dev-backend`, `yuviron-dev-media-worker`, `yuviron-dev-nginx`. Образ берётся через `docker inspect --format '{{.Image}}'` запущенного контейнера — сканируется именно то, что сейчас работает. `--exit-code 0`: находки видны в логах, но не блокируют деплой.

**Deploy prod (блокирующее):** те же три образа с `--exit-code 1`. При нахождении fixable CRITICAL/HIGH CVE хотя бы в одном образе шаг завершается с ошибкой до smoke test; это автоматически тригерит существующий rollback.

Кеш Trivy DB: `/tmp/trivy-cache` на runner, монтируется в контейнер при каждом запуске — повторные сканы быстрее.

---

## GitHub Actions и self-hosted runner

В инфраструктуре используется **self-hosted runner**, который запускается на сервере.

Это позволяет:

* запускать CI-задачи
* выполнять `./scripts/cli.py stack preflight <env>`
* собирать backend/frontend
* выполнять deploy dev/prod
* не использовать платные GitHub-hosted runners для тяжёлых задач деплоя

Для frontend-части можно реализовать деплой **только для затронутых приложений**, чтобы:

* не пересобирать весь frontend без необходимости
* ускорять обновление dev-окружения
* уменьшать лишнюю нагрузку на сервер

Для ручного обновления одного frontend-сервиса можно использовать:

```bash
./shared/frontend/scripts/deploy-frontend-service.sh admin dev
```

Скрипт использует актуальную compose-схему: `infra/compose.yml`, `generated/<env>/compose.frontends.yml` и `generated/<env>/deploy.env`.

---

## Создание self-hosted runner

Runner создаётся на уровне организации.

Открыть:

```text
GitHub -> Organization -> Settings -> Actions -> Runners
```

Нажать:

```text
New runner
```

Выбрать:

```text
Linux
Architecture: x64
```

GitHub сгенерирует инструкции установки.

---

## Установка runner

Подключиться к серверу.

```bash
cd /opt
mkdir actions-runner
cd actions-runner
```

Скачать runner:

```bash
curl -o actions-runner-linux-x64.tar.gz -L https://github.com/actions/runner/releases/latest/download/actions-runner-linux-x64.tar.gz
```

Распаковать:

```bash
tar xzf actions-runner-linux-x64.tar.gz
```

---

## Настройка runner

GitHub выдаёт команду конфигурации.

Пример:

```bash
./config.sh --url https://github.com/JByte-organization --token <TOKEN>
```

Скрипт может задать вопросы:

```text
Enter the name of runner
Enter runner group
Enter labels
```

Можно оставить значения по умолчанию или указать собственные labels.

---

## Запуск runner

```bash
./run.sh
```

---

## Запуск runner как сервиса

Чтобы runner запускался автоматически после перезагрузки сервера:

```bash
sudo ./svc.sh install
sudo ./svc.sh start
```

Проверка статуса:

```bash
sudo ./svc.sh status
```

---

## Проверка runner

Открыть:

```text
GitHub -> Organization -> Settings -> Actions -> Runners
```

Runner должен иметь статус:

```text
Online
```

---

## Использование runner в workflow

Чтобы GitHub Actions использовал self-hosted runner:

```yaml
runs-on: [self-hosted, yuviron]
```

Готовые shared workflow находятся в:

```text
shared/backend/deploy.yml
shared/frontend/.github/workflows/deploy.yml
```

Пример шага workflow:

```yaml
jobs:
  deploy:
    runs-on: [self-hosted, yuviron]

    steps:
      - name: Update source
        run: |
          cd /opt/yuviron-server/src/yuviron-frontend
          git fetch origin dev
          git reset --hard origin/dev

      - name: Install dependencies
        run: |
          cd /opt/yuviron-server/src/yuviron-frontend
          pnpm install --frozen-lockfile

      - name: Deploy app
        run: |
          cd /opt/yuviron-server/src/yuviron-frontend
          pnpm --filter admin run deploy
```

В более полной схеме workflow может делать:

1. checkout/update исходников
2. `./scripts/cli.py stack preflight <env>`
3. build
4. affected detection
5. deploy только нужных приложений
6. перезапуск соответствующего окружения

---

## Требования для runner

Self-hosted runner должен:

* запускаться не от `root`
* иметь доступ к Docker
* входить в группу `docker`

Проверка групп пользователя:

```bash
groups
```

Если пользователь не входит в группу `docker`:

```bash
sudo usermod -aG docker $USER
```

После этого необходимо перелогиниться.

---

## Права на директорию runner

Если runner расположен в `/opt/actions-runner`, директория должна принадлежать пользователю, от имени которого он запускается.

Пример:

```bash
sudo chown -R <RUNNER_USER>:<RUNNER_USER> /opt/actions-runner
```

Это гарантирует, что runner сможет:

* запускать workflow
* записывать данные в каталог `_work`
* обновляться без ошибок прав доступа

Если runner был установлен сразу от имени нужного пользователя, дополнительная настройка прав может не потребоваться.

---
