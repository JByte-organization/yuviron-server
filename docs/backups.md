# Бэкапы

Создание, проверка восстановления, переменные окружения, cron, ротация и структура архивов.

[← К README](../README.md)

## Бэкапы

В инфраструктуре реализована система резервного копирования.

### Что покрывается

| Компонент | Покрывается | Метод |
|---|---|---|
| MySQL | ✅ | Логический дамп (`mysqldump`) |
| storage (файлы) | ✅ | `tar.gz` bind-mount директории |
| Redis | ✅ | Снимок named volume |
| RabbitMQ | ✅ | Снимок named volume |
| ClickHouse | ⚠️ частично | Shadow backup (`FREEZE`) — **ручное восстановление** |

> **ClickHouse:** архив содержит data parts из shadow-директории (`BACKUP FREEZE`), но **не содержит** DDL (CREATE TABLE) и метаданные. `backup restore` намеренно не восстанавливает ClickHouse автоматически — подробнее см. [раздел ниже](#clickhouse-ограничения).

### Переменные окружения

```env
BACKUP_ROOT=./backups
BACKUP_TMP=./backups/tmp
BACKUP_LOG_DIR=./backups/logs
BACKUP_ARCHIVE_DIR=./backups/archives
BACKUP_RESTORE_TEST_TMP=./backups/restore-test
BACKUP_RESTORE_TEST_AFTER_CREATE=1
RESTORE_TEST_MIN_TABLES=1

BACKUP_ENVS=prod

BACKUP_STORAGE_DEV=./storage/dev
BACKUP_STORAGE_PROD=./storage/prod

# Offsite: транспорт и адрес (подробнее см. раздел «Offsite-копирование»)
BACKUP_REMOTE_TRANSPORT=
BACKUP_REMOTE_PATH=
BACKUP_SSH_KEY_FILE=
BACKUP_S3_STORAGE_CLASS=

BACKUP_RETENTION_DAYS=14

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

### ClickHouse — ограничения

> **⚠️ ClickHouse НЕ восстанавливается автоматически через `backup restore`.**

`backup create` сохраняет ClickHouse через `ALTER TABLE ... FREEZE`, результат кладётся в `clickhouse_shadow.tar.gz` внутри архива. Shadow backup содержит **только data parts** (файлы MergeTree-партиций) — без DDL и системных метаданных. Автоматическое восстановление не реализовано намеренно: без CREATE TABLE воссоздать таблицы невозможно, а частичный `ATTACH` без схемы приведёт к ошибке.

**Что означает при restore:**
- MySQL, Redis, RabbitMQ, storage — восстанавливаются полностью.
- ClickHouse — стартует **с пустыми таблицами** (структура сохраняется, данные теряются).
- `clickhouse_shadow.tar.gz` остаётся в архиве для ручного восстановления.

**Ручное восстановление ClickHouse после `backup restore`:**

1. Убедись, что ClickHouse запущен и таблицы созданы (DDL применяется при старте сервиса через миграции или init-скрипты):

   ```bash
   docker exec -it <project>-clickhouse clickhouse-client
   SHOW TABLES FROM yuviron_analytics;
   ```

2. Извлеки shadow backup из архива:

   ```bash
   tar -xzf backups/archives/<archive>.tar.gz --wildcards '*/clickhouse_shadow.tar.gz' -O \
     | tar -xzf - -C /tmp/ch-restore/
   ```

3. Для каждой таблицы скопируй parts в `detached/` и прикрепи:

   ```bash
   # Пример для таблицы yuviron_analytics.events
   docker cp /tmp/ch-restore/<part_dir>/ <project>-clickhouse:/var/lib/clickhouse/data/yuviron_analytics/events/detached/
   docker exec -it <project>-clickhouse clickhouse-client \
     --query "ALTER TABLE yuviron_analytics.events ATTACH PARTITION <partition_id>"
   ```

4. Проверь количество строк после каждой таблицы.

**RPO для аналитики:** данные ClickHouse можно потерять, если DDL не зафиксирован в репозитории или init-скрипте. Убедись, что все CREATE TABLE хранятся в `infra/clickhouse/` или аналогичной директории.

---

### Автоматизация (cron)

```bash
./scripts/cli.py tools setup-cron
```

Интерактивный скрипт. Задаёт вопросы о расписании и устанавливает в crontab два задания:

* `backup create` - ежедневно в заданное время; по умолчанию `03:15`
* `backup verify` - еженедельно в заданный день/время; по умолчанию воскресенье `05:00`

Логи пишутся в `backups/logs/cron-backup.log` и `backups/logs/cron-restore-test.log`. Повторный запуск заменяет предыдущие cron-строки проекта без дублей.

### Offsite-копирование

Поддерживаются четыре транспорта. Транспорт автодетектируется по формату `BACKUP_REMOTE_PATH` или задаётся явно через `BACKUP_REMOTE_TRANSPORT`.

#### Вариант 1 — локальный диск / NFS / SSHFS (`local`)

Смонтируй удалённое хранилище локально и укажи путь к точке монтирования:

```env
BACKUP_REMOTE_PATH=/mnt/nas/backups
```

Автодетект: любой путь без `@` и `://` → `local`.

---

#### Вариант 2 — rsync по SSH (`rsync`)

Предпочтительный вариант для удалённых серверов: создаёт целевую директорию автоматически, передаёт только изменения.

```env
BACKUP_REMOTE_PATH=backup@nas.example.com:/srv/backups/
# BACKUP_REMOTE_TRANSPORT=rsync  # опционально — автодетект по формату
```

С явным SSH-ключом:

```env
BACKUP_REMOTE_PATH=backup@nas.example.com:/srv/backups/
BACKUP_SSH_KEY_FILE=/home/deploy/.ssh/backup_ed25519
```

Без `BACKUP_SSH_KEY_FILE` используется системный SSH-агент или `~/.ssh/config`.

Убедись что хост добавлен в `known_hosts`, иначе первый запуск из cron зависнет:

```bash
ssh-keyscan nas.example.com >> ~/.ssh/known_hosts
```

---

#### Вариант 3 — scp по SSH (`scp`)

Проще rsync, но **не создаёт целевую директорию** — она должна существовать. Если нужно создание директории — используй rsync.

```env
BACKUP_REMOTE_PATH=backup@nas.example.com:/srv/backups/
BACKUP_REMOTE_TRANSPORT=scp
BACKUP_SSH_KEY_FILE=/home/deploy/.ssh/backup_ed25519
```

---

#### Вариант 4 — AWS S3 (`s3`)

Требует установленный `aws` CLI и настроенные credentials.

```env
BACKUP_REMOTE_PATH=s3://my-company-backups/prod/
```

Credentials задаются любым стандартным способом AWS:

```env
# Env-переменные (для серверов без IAM-роли):
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=eu-central-1
```

Или через IAM Instance Role (на EC2 — автоматически, ничего дополнительного не нужно).

**Дешёвое хранение** — для бэкапов которые читаются редко:

```env
BACKUP_S3_STORAGE_CLASS=GLACIER_IR   # ~$0.004/GB/мес, мгновенный доступ
# BACKUP_S3_STORAGE_CLASS=DEEP_ARCHIVE  # ~$0.001/GB/мес, доступ через 12ч
```

---

#### Общие примечания

- Ошибка offsite (нет сети, неверный ключ, нет доступа к бакету) **не прерывает бэкап** — локальный архив сохраняется, ошибка пишется в лог как `[WARN]`.
- Offsite-копирование выполняется только после успешного restore-test.
- Адрес валидируется до начала бэкапа — ошибки конфигурации обнаруживаются сразу.

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

---

## Disaster Recovery

### Целевые показатели

| Показатель | Значение | Условие |
|---|---|---|
| **RPO** (максимальная потеря данных) | ≤ 24 часа | Cron запускает `backup create` раз в сутки. Снизить до ≤ 1 ч — увеличить частоту cron. |
| **RTO** (целевое время восстановления) | ≤ 2 часа | На типовом VPS: 10–20 мин на перепровизию + 5–30 мин на импорт данных в зависимости от размера БД. |

### Полная потеря сервера — порядок действий

1. **Провизия нового сервера** — установи Docker, Docker Compose, Python 3. Клонируй репозиторий.

2. **Восстанови конфиг** — скопируй `env/prod.env` из резервной копии (хранится в `deploy.env` внутри архива или отдельно, если есть внешняя секретница).

3. **Скачай последний архив**:

   ```bash
   # Если offsite — scp/aws s3 cp/rsync из удалённого хранилища
   scp backup@nas:/srv/backups/backup_*.tar.gz backups/archives/
   ```

4. **Проверь архив**:

   ```bash
   ./scripts/cli.py backup verify --archive backups/archives/<archive>.tar.gz --full
   ```

5. **Восстанови данные**:

   ```bash
   ./scripts/cli.py backup restore --env prod --archive backups/archives/<archive>.tar.gz --force
   ```

6. **Подними стек**:

   ```bash
   ./scripts/cli.py stack up prod
   ```

7. **Проверь healthcheck** через `./scripts/cli.py tools docker-status prod`.

### Важно о секретах в архиве

Файл `deploy.env` включается в архив backup в открытом виде — он содержит все секреты окружения (пароли БД, Stripe, JWT). При загрузке на offsite-хранилище убедись что:

- доступ к хранилищу ограничен (S3 bucket policy / SSH-ключ)
- для S3 включено server-side encryption (`SSE-S3` или `SSE-KMS`)
- архивы не попадают в публичные места

Если секреты скомпрометированы — ротируй их до подъёма стека.
