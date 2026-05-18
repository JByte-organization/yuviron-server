# Ротация паролей и credentials

Как сменить пароли для nginx Basic Auth, Seq, Aspire Dashboard, MySQL и RabbitMQ без пересоздания окружения.

[← К README](../README.md)

## nginx Basic Auth (Seq, Aspire, Backoffice)

htpasswd создаётся один раз при `init.py` / `generate-config.py` и не перезаписывается автоматически.

Посмотреть текущие credentials:

```bash
cat generated/dev/htpasswd.credentials
cat generated/prod/htpasswd.credentials
```

Сменить пароль:

```bash
./scripts/cli.py tools rotate-htpasswd dev
./scripts/cli.py tools rotate-htpasswd prod
```

Новые credentials выводятся в stdout и сохраняются в `generated/<env>/htpasswd.credentials`. Старый файл удаляется, новый записывается атомарно.

nginx перечитывает htpasswd при каждом аутентифицированном запросе - **перезапуск контейнера не нужен**.

Ротация htpasswd затрагивает Basic Auth для всех management routes с `basic_auth`: Seq, Aspire Dashboard, Backoffice и других. Пароль администратора Seq через htpasswd не меняется - у него отдельный механизм.

---

## Seq admin password

`SEQ_FIRSTRUN_ADMINPASSWORDHASH` из `env/<env>.env` применяется **только при первом старте** до инициализации Seq DB. Изменение этой переменной у работающего экземпляра ничего не меняет.

Для смены пароля работающего Seq:

1. Открыть веб-интерфейс: `https://dev-seq.<domain>` или `https://seq.<domain>`
2. Перейти в **Settings -> Users** -> сменить пароль через UI

Если нужно обновить hash для будущих fresh installs (например, при развёртывании на новом сервере):

```bash
./scripts/cli.py tools seq-hash
```

Скопируй выведенный hash в `SEQ_FIRSTRUN_ADMINPASSWORDHASH` в `env/<env>.env`.

---

## Aspire Dashboard tokens

`ASPIRE_FRONTEND_BROWSER_TOKEN` (токен для входа в UI) и `ASPIRE_OTLP_API_KEY` (ключ для OTLP ingest) хранятся в `env/<env>.env` и применяются при каждом старте контейнера.

Смена:

```bash
# 1. Обнови токены в env/<env>.env

# 2. Перегенерируй runtime config
python3 scripts/generate-config.py --env prod --domain yuviron.com

# 3. Перезапусти только aspire-dashboard
docker compose \
  --env-file ./generated/prod/deploy.env \
  -f ./infra/compose.yml \
  -f ./generated/prod/compose.frontends.yml \
  -p yuviron-prod \
  restart aspire-dashboard
```

Полный перезапуск всего стека не нужен.

---

## MySQL password

Смена пароля MySQL требует согласованного обновления - в env-файле и внутри самой БД.

```bash
# 1. Обнови MYSQL_PASSWORD (и MYSQL_ROOT_PASSWORD при необходимости) в env/<env>.env

# 2. Перегенерируй runtime config
python3 scripts/generate-config.py --env <env> --domain yuviron.com

# 3. Смени пароль внутри работающей БД
docker exec -it yuviron-<env>-mysql mysql -uroot -p<OLD_ROOT_PASSWORD> \
  -e "ALTER USER 'yuviron'@'%' IDENTIFIED BY '<NEW_PASSWORD>'; FLUSH PRIVILEGES;"

# 4. Перезапусти backend и media-worker, чтобы они подхватили новую connection string
./scripts/cli.py stack down <env>
./scripts/cli.py stack up <env>
```

---

## RabbitMQ password

```bash
# 1. Обнови RABBITMQ_DEFAULT_PASS в env/<env>.env

# 2. Перегенерируй runtime config
python3 scripts/generate-config.py --env <env> --domain yuviron.com

# 3. Сброс пароля внутри работающего брокера
docker exec -it yuviron-<env>-rabbitmq rabbitmqctl change_password <user> <NEW_PASSWORD>

# 4. Перезапусти сервисы
./scripts/cli.py stack down <env>
./scripts/cli.py stack up <env>
```

---

## Итого: что требует перезапуска

| Компонент | Команда | Перезапуск |
|---|---|---|
| nginx Basic Auth | `tools rotate-htpasswd <env>` | Не нужен - nginx читает htpasswd на каждый запрос |
| Seq admin password | Seq Settings -> Users в web UI | Не нужен |
| Seq hash для fresh install | `tools seq-hash` -> обновить `SEQ_FIRSTRUN_ADMINPASSWORDHASH` | При следующем fresh install |
| Aspire Browser Token | Обновить env -> `generate-config` -> `restart aspire-dashboard` | Только aspire-dashboard |
| Aspire OTLP Key | То же | Только aspire-dashboard |
| MySQL password | Обновить env + ALTER USER -> полный restart | Весь стек |
| RabbitMQ password | Обновить env + `change_password` -> полный restart | Весь стек |