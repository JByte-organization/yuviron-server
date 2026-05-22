# Бэкапы

Создание, проверка восстановления, переменные окружения, cron, ротация и структура архивов.

[← К README](../README.md)

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
BACKUP_RESTORE_TEST_AFTER_CREATE=1
RESTORE_TEST_MIN_TABLES=1

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
REDIS_SERVICE_NAME=redis
REDIS_BGSAVE_TIMEOUT=30
```

### Команды

```text
./scripts/cli.py backup create
./scripts/cli.py backup verify
./scripts/cli.py backup verify --full
./scripts/cli.py backup restore-test dev
./scripts/cli.py backup restore-test dev --archive backups/archives/<archive>.tar.gz
./scripts/cli.py backup restore --env dev --archive backups/archives/<archive>.tar.gz --force
./scripts/cli.py tools setup-cron
```

### Создание бэкапа

```bash
./scripts/cli.py backup create
```

Перед дампом MySQL запускается `mysqlcheck --all-databases --check --silent`: если обнаружены проблемные таблицы, они фиксируются в `metadata.json` как предупреждения, но дамп продолжается.

Перед снятием Redis volume snapshot команда отправляет `BGSAVE` и ожидает завершения фонового сохранения (по умолчанию до 30 секунд, управляется `REDIS_BGSAVE_TIMEOUT`). Ожидание построено на `LASTSAVE`: код снимает timestamp до `BGSAVE`, затем опрашивает Redis каждую секунду — как только `LASTSAVE` увеличился, `dump.rdb` готов и снимается snapshot volume. Если Redis недоступен или таймаут истёк, предупреждение фиксируется в metadata, но snapshot тома снимается всё равно.

После создания финального `backup_*.tar.gz` команда автоматически извлекает этот архив в `BACKUP_RESTORE_TEST_TMP`, поднимает временный `mysql:8.4`, импортирует каждый включённый `mysql.sql.gz` в отдельную временную базу и проверяет минимум восстановленных таблиц. Redis volume archive при создании и `backup verify` дополнительно проверяется на наличие непустого AOF/RDB-файла. Off-site copy и ротация выполняются только после успешного restore-test.

Порог таблиц можно задать флагом или переменной:

```bash
./scripts/cli.py backup create --restore-test-min-tables 5
```

```env
RESTORE_TEST_MIN_TABLES=5
```

Для аварийного ручного запуска проверку можно отключить:

```bash
./scripts/cli.py backup create --skip-restore-test
```

```env
BACKUP_RESTORE_TEST_AFTER_CREATE=0
```

### Проверка восстановления

```bash
./scripts/cli.py backup verify
./scripts/cli.py backup verify --full
./scripts/cli.py backup verify --archive backups/archives/<archive>.tar.gz
```

По умолчанию берёт последний архив из `backups/archives/`. Что проверяется:

* целостность архива как `tar.gz`
* наличие и валидность `metadata.json` - предупреждения в metadata означают неполный backup
* `deploy.env` - обязательный ключ `COMPOSE_PROJECT_NAME`
* `routes.env` - наличие `client` маршрута
* `mysql.sql.gz` - gzip-целостность дампа
* `storage.tar.gz` - целостность tar-архива хранилища
* `volume_redis_data.tar.gz` - наличие непустого AOF/RDB файла внутри

Флаги:

* `--full` - дополнительно поднимает временный `mysql:8.4` и импортирует каждый MySQL dump в отдельную базу, затем проверяет минимум восстановленных таблиц; аналогичен `restore-test`, но обходит все окружения из архива
* `--archive <path>` - явно указать архив вместо последнего

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

### Полное восстановление из архива

```bash
./scripts/cli.py backup restore --env dev --archive backups/archives/<archive>.tar.gz --force
./scripts/cli.py backup restore --env prod --archive backups/archives/<archive>.tar.gz --force
```

Полное деструктивное восстановление окружения: останавливает compose-стек, восстанавливает MySQL dump, storage и named volumes (`mysql_data`, `redis_data`, `rabbitmq_data`) из указанного архива. Флаг `--force` обязателен - команда не запустится без явного подтверждения намерения. `--env` и `--archive` можно не передавать: CLI спросит интерактивно.

Перед полным restore рекомендуется проверить архив через `backup verify --full`.

---

### Автоматизация (cron)

```bash
./scripts/cli.py tools setup-cron
```

Интерактивный скрипт. Задаёт вопросы о расписании и устанавливает в crontab два задания:

* `backup create` - ежедневно в заданное время; по умолчанию `03:15`
* `backup verify` - еженедельно в заданный день/время; по умолчанию воскресенье `05:00`

Логи пишутся в `backups/logs/cron-backup.log` и `backups/logs/cron-restore-test.log`. Повторный запуск заменяет предыдущие cron-строки проекта без дублей.

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

Архивы из `backups/archives/` старше указанного количества дней удаляются автоматически после успешного `backup create` - уже после off-site copy и restore-test. Значение `14` означает хранить две последних недели. Для отключения авторотации выставить большое значение, например `BACKUP_RETENTION_DAYS=365`.

### Структура

```text
backups/
├── tmp/
├── logs/
├── archives/
└── restore-test/
```

Директория `backups` добавлена в `.gitignore`.
