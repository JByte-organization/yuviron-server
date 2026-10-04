# scripts/

Вспомогательные скрипты для CI/CD пайплайна бекенда.

---

## generate_appsettings.py

### Что это и зачем

Скрипт генерирует файлы `appsettings.{env}.json` для `Yuviron.Api` и `Yuviron.MediaWorker`.

Эти файлы содержат секреты и **никогда не коммитятся** — они добавлены в `.gitignore`. Скрипт является единственным источником правды об их структуре.

Вызывается автоматически шагом **«Generate appsettings»** в `.github/workflows/deploy.yml` сразу после синхронизации исходников и до сборки Docker-образов.

> **До:** логика генерации жила как inline Python-heredoc прямо в YAML воркфлоу — нетестируемый, нечитаемый, ломкий.  
> **После:** отдельный файл, который можно читать, ревьюить и при необходимости тестировать изолированно.

### Что генерируется

```
src/Yuviron.Api/appsettings.{ASPNET_ENV}.json
src/Yuviron.MediaWorker/appsettings.{ASPNET_ENV}.json
```

Запись происходит атомарно — сначала во временный файл, затем `os.replace()`. Права `0600` (только owner) — секреты не видны в полузаписанном состоянии и не доступны другим пользователям системы.

---

### Как запускается

```bash
# Воркфлоу делает это автоматически. Вручную (для отладки):
cd /opt/yuviron-server/src/yuviron-backend

export DEPLOY_ENV=dev
export ASPNET_ENV=Development
export JWT_SECRET=...
# ... остальные переменные (см. список ниже)

python3 scripts/generate_appsettings.py
```

---

### Как вносить изменения

#### Добавить несекретное значение (лимит, таймаут, флаг)

Просто добавить в `_api_config()` или `_worker_config()`. Больше ничего трогать не нужно.

```python
# Пример — новая секция в конфиге API:
"RateLimits": {
    "MaxRequestsPerMinute": 100,
},
```

#### Сделать значение разным для prod и dev

Внутри `_api_config()` уже доступен флаг `is_prod`:

```python
"FeatureFlags": {
    "EnableExperimentalSearch": not is_prod,
},
"Cache": {
    "TtlSeconds": 3600 if is_prod else 30,
},
```

#### Добавить новый секрет

Три шага:

**1. Добавить в скрипт** — вызвать `_env("MY_NEW_SECRET")` в нужном месте конфига:

```python
"ThirdPartyApi": {
    "ApiKey": _env("MY_NEW_SECRET"),
},
```

**2. Добавить в воркфлоу** — в блок `env:` шага «Generate appsettings» в `.github/workflows/deploy.yml`:

```yaml
- name: Generate appsettings
  env:
    # ... уже существующие переменные ...
    MY_NEW_SECRET: ${{ secrets.MY_NEW_SECRET }}
```

**3. Создать GitHub Secret** — в настройках репозитория:  
`Settings → Secrets and variables → Actions → New repository secret`

Сделать для каждого окружения (dev и prod), если значения отличаются.

#### Удалить секрет

1. Удалить вызов `_env("OLD_SECRET")` из конфига в скрипте.
2. Удалить соответствующую строку из блока `env:` в `deploy.yml`.

GitHub Secret можно оставить — неиспользуемые секреты безвредны.

#### Добавить конфиг нового сервиса

1. Написать новую функцию `_myservice_config()` по аналогии с `_api_config()`.
2. Добавить вызов `_write_atomic(...)` в `main()`:

```python
_write_atomic(
    ROOT / "src" / "Yuviron.MyService" / f"appsettings.{aspnet_env}.json",
    _myservice_config(),
)
```

3. Добавить все новые переменные в список ниже и в `env:` блок воркфлоу.

---

### Переменные окружения

Все переменные инжектируются воркфлоу из GitHub Secrets. На сервере вручную не выставляются.

| Переменная | Описание |
|---|---|
| `DEPLOY_ENV` | `dev` или `prod` |
| `ASPNET_ENV` | `Development` или `Production` |
| `JWT_SECRET` | Секрет для подписи JWT-токенов |
| `EMAIL_USERNAME` | Логин SMTP-аккаунта (Gmail) |
| `EMAIL_PASSWORD` | Пароль SMTP-аккаунта |
| `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` | Сид-пользователь Admin |
| `SEED_MANAGER_EMAIL` / `SEED_MANAGER_PASSWORD` | Сид-пользователь Manager |
| `SEED_USER_EMAIL` / `SEED_USER_PASSWORD` | Сид-пользователь User |
| `SEED_PREMIUM_EMAIL` / `SEED_PREMIUM_PASSWORD` | Сид-пользователь Premium |
| `JAMENDO_CLIENT_ID` | Client ID для Jamendo API |
| `STREAM_SECRET` | Секрет для подписи стриминговых URL |
| `STRIPE_SECRET_KEY` | Секретный ключ Stripe |
| `STRIPE_WEBHOOK_SECRET` | Секрет для верификации Stripe webhook |
