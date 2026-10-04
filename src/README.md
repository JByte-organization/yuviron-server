# Source code

В этой директории находятся исходные проекты **Yuviron**, которые используются инфраструктурой сервера для сборки Docker-образов.

Каждый сервис хранится в **отдельном Git-репозитории**, а папка `src` используется как build context при сборке контейнеров.

---

## Проекты

### Backend (`src/yuviron-backend`)

.NET 9 API-сервер и фоновый обработчик медиа.

**Repository:** https://github.com/JByte-organization/yuviron-backend

**Dockerfile:** `infra/docker/dotnet/Dockerfile`

Один multi-stage Dockerfile собирает три образа в зависимости от target:

| Target | Сервис | Порт |
|---|---|---|
| `backend` | Yuviron.Api — основной HTTP API | 5073 |
| `media-worker` | Yuviron.MediaWorker — фоновая обработка медиафайлов | 5074 |
| `migrator` | EF Core migrator — запускается как одноразовый контейнер | — |

Имена контейнеров в dev-окружении:

```
<COMPOSE_PROJECT_NAME>-backend
<COMPOSE_PROJECT_NAME>-media-worker
<COMPOSE_PROJECT_NAME>-migrator
```

`COMPOSE_PROJECT_NAME` задаётся автоматически при генерации конфига и по умолчанию равен `<project_name>-<env>` (например `yuviron-dev`).

---

### Frontend (`src/yuviron-frontend`)

Next.js монорепозиторий с несколькими приложениями (Turborepo + pnpm).

**Repository:** https://github.com/JByte-organization/yuviron-frontend

**Dockerfile для Next.js приложений:** `infra/docker/frontend-next/Dockerfile`

Один Dockerfile собирает любое из приложений через build arg `APP_NAME`:

| `APP_NAME` | Приложение | Описание |
|---|---|---|
| `client-app` | Клиентское приложение | Публичный интерфейс |
| `admin` | Admin панель | Управление платформой |
| `backoffice` | Backoffice | Внутренние операции |

**Dockerfile для статических приложений:** `infra/docker/frontend-static/Dockerfile`

Используется для приложений которые собираются в статику (без SSR).

Имена контейнеров в dev-окружении:

```
<COMPOSE_PROJECT_NAME>-client-app
<COMPOSE_PROJECT_NAME>-admin
<COMPOSE_PROJECT_NAME>-backoffice
```

Список запущенных приложений определяется при генерации конфига через `--apps`.

---

## Структура директории

```
src/
├── yuviron-backend/    — .NET 9 backend (API + MediaWorker)
└── yuviron-frontend/   — Next.js монорепозиторий (Turborepo)
    ├── apps/
    │   ├── client-app/
    │   ├── admin/
    │   └── backoffice/
    └── packages/       — shared: api-client, ui, store, typescript-config
```

---

## Как используется эта директория

Все Dockerfile-ы находятся в `infra/docker/` и используют `src/` как часть build context.

Сборка происходит автоматически при `./scripts/cli.py stack up` — Docker Compose собирает образы из исходников.

| Dockerfile | Build context |
|---|---|
| `infra/docker/dotnet/Dockerfile` | корень проекта (`..`) |
| `infra/docker/frontend-next/Dockerfile` | `src/yuviron-frontend/` |
| `infra/docker/frontend-static/Dockerfile` | `src/yuviron-frontend/` |
| `infra/edge/Dockerfile` | `infra/edge/` |

---

## Важно

В этой директории хранится **только исходный код сервисов**.

- Инфраструктура (compose, Dockerfile-ы, nginx): `infra/`
- Edge nginx (reverse proxy): `infra/edge/`
- Скрипты управления: `scripts/`
- Сгенерированные runtime-конфиги: `generated/`

---

## Обновление исходников

```bash
cd src/yuviron-backend && git pull
cd ../yuviron-frontend && git pull
```

После обновления пересобрать контейнеры:

```bash
./scripts/cli.py stack up dev
```
