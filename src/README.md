# Source code

В этой директории находятся исходные проекты **Yuviron**, которые используются инфраструктурой сервера для сборки Docker образов.

Каждый сервис хранится в **отдельном Git-репозитории**, а папка `src` используется как рабочая директория, из которой инфраструктура собирает контейнеры.

---

## Проекты

### Backend

API сервер проекта.

**Repository**

https://github.com/JByte-organization/yuviron-backend

Используется для сборки Docker образа:

```
infra/docker/backend/Dockerfile
```

Контейнер, который запускается в dev окружении:

```
yuviron-dev-backend
```

---

### Frontend

Web интерфейс приложения.

**Repository**

https://github.com/JByte-organization/yuviron-frontend

Используется для сборки Docker образа:

```
infra/docker/frontend/Dockerfile
```

Контейнер, который запускается в dev окружении:

```
yuviron-dev-frontend
```

---

## Структура директории

```
src/
├── yuviron-backend
└── yuviron-frontend
```

---

## Как используется эта директория

Папка `src` используется инфраструктурой проекта для сборки Docker образов.

Dockerfile находятся в директории:

```
infra/docker/
```

Во время сборки Docker контейнеров используется код из:

```
src/yuviron-backend
src/yuviron-frontend
```

---

## Важно

В этой директории хранится **только исходный код сервисов**.

Инфраструктура проекта находится в:

```
infra/
```

Reverse proxy и внешний nginx находятся в:

```
edge/
```

---

## Обновление исходников

Чтобы обновить код сервисов, необходимо обновить соответствующие Git-репозитории.

Пример:

```
cd src/yuviron-backend
git pull

cd ../yuviron-frontend
git pull
```

После обновления исходников необходимо пересобрать контейнеры.
