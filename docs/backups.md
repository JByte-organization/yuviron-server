# Бэкапы

Создание, проверка восстановления, переменные окружения, cron, ротация и структура архивов.

[← К README](../README.md)

## 💾 BACKUP

### Создание

```bash
./scripts/cli.py backup create
```

### Проверка восстановления

```bash
./scripts/cli.py backup verify
./scripts/cli.py backup verify --full
./scripts/cli.py backup restore-test dev
```

### Полный цикл

```bash
./scripts/cli.py backup create && ./scripts/cli.py backup verify
```

---

## Бэкапы

В инфраструктуре реализована система резервного копирования.

### Что покрывается

* MySQL (дампы базы)
* storage (файлы)
* единые архивы
* тест восстановления

### Переменные окружения

```env
BACKUP_ROOT=./backups
BACKUP_TMP=./backups/tmp
BACKUP_LOG_DIR=./backups/logs
BACKUP_ARCHIVE_DIR=./backups/archives
BACKUP_RESTORE_TEST_TMP=./backups/restore-test

BACKUP_ENVS=dev,prod

BACKUP_STORAGE_DEV=./storage/dev
BACKUP_STORAGE_PROD=./storage/prod

BACKUP_REMOTE_PATH=

BACKUP_RETENTION_DAYS=14

BACKUP_PROJECT_NAME=yuviron-server

COMPOSE_FILE=infra/compose.yml

COMPOSE_PROJECT_DEV=yuviron_dev
COMPOSE_PROJECT_PROD=yuviron_prod

MYSQL_SERVICE_NAME=mysql
BACKEND_SERVICE_NAME=backend
```

### Команды

```text
./scripts/cli.py backup create
./scripts/cli.py backup verify
./scripts/cli.py backup verify --full
./scripts/cli.py backup restore-test dev
./scripts/cli.py backup restore --env dev --archive backups/archives/<archive>.tar.gz
./scripts/cli.py tools setup-cron
```

### Создание бэкапа

```bash
./scripts/cli.py backup create
```

### Проверка восстановления

```bash
./scripts/cli.py backup verify
```

### Restore-test MySQL dump

```bash
./scripts/cli.py backup restore-test dev
./scripts/cli.py backup restore-test prod --archive backups/archives/<archive>.tar.gz
```

Команда берёт `mysql.sql.gz` выбранного окружения из backup-архива, поднимает временный контейнер `mysql:8.4`, импортирует dump в отдельную временную базу, проверяет количество восстановленных таблиц и удаляет временный контейнер/рабочую директорию.

По умолчанию требуется минимум одна таблица. Порог можно переопределить:

```bash
./scripts/cli.py backup restore-test dev --min-tables 5
```

### Автоматизация (cron)

```bash
./scripts/cli.py tools setup-cron
```

### Off-site copy

`BACKUP_REMOTE_PATH` поддерживает только локальный directory path. Для rsync/scp/NFS/S3/rclone-подобных сценариев сначала смонтируй remote storage локально, затем укажи путь к mount directory, например:

```env
BACKUP_REMOTE_PATH=/mnt/yuviron-backups
```

Значение валидируется до запуска backup: remote specs вроде `user@host:/path`, URL, whitespace и shell metacharacters отклоняются.

### Ротация

```env
BACKUP_RETENTION_DAYS=14
```

### Структура

```text
backups/
├── tmp/
├── logs/
├── archives/
└── restore-test/
```

Директория `backups` добавлена в `.gitignore`.
