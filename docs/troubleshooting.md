# Диагностика и типичные проблемы

Инструменты диагностики, частые ошибки preflight/smoke и способы их устранения.

[← К README](../README.md)

## Быстрая диагностика

### Проверить конфигурацию и состояние стека

```bash
./scripts/cli.py stack preflight dev    # проверить конфигурацию до запуска
./scripts/cli.py stack smoke dev        # проверить уже запущенный стек
./scripts/cli.py doctor dev             # проверить host-level готовность
```

### Статус контейнеров

```bash
docker ps
```

Ожидаемые контейнеры запущенного стека:

```text
yuviron-dev-nginx
yuviron-dev-mysql
yuviron-dev-redis
yuviron-dev-rabbitmq
yuviron-dev-backend
yuviron-dev-media-worker
yuviron-dev-client-app
yuviron-dev-backoffice
yuviron-dev-admin
yuviron-dev-seq
yuviron-dev-aspire-dashboard
```

### Логи контейнера

```bash
docker logs yuviron-dev-backend
docker logs yuviron-dev-nginx
docker logs -f yuviron-dev-backend --tail=100
```

### Проверить nginx конфиг

```bash
docker exec yuviron-dev-nginx nginx -t
```

### Ресурсы и диск

```bash
docker stats --no-stream          # CPU/RAM контейнеров
./scripts/cli.py tools docker-clean --mode report   # диск Docker
df -h                             # диск хоста
```

---

## Ошибки preflight

### `source is stale or modified`

Source-файлы обновились (например, после `git pull`), а `generated/<env>/` устарела.

**Dev** - preflight пересобирает автоматически:

```bash
./scripts/cli.py stack preflight dev
```

**Prod** - нужно явное разрешение:

```bash
./scripts/cli.py stack preflight prod --allow-regenerate
# или
ALLOW_REGENERATE=1 ./scripts/cli.py stack preflight prod
```

Ручная перегенерация в любой момент:

```bash
python3 scripts/init.py --env dev --domain yuviron.com --no-up
```

---

### `Network yuviron_shared not found`

```bash
docker network create yuviron_shared
```

---

### `Port 80/443 already in use`

Если порт занят ожидаемым контейнером (`yuviron-dev-nginx` или `yuviron-prod-nginx`) - это нормально, preflight это учитывает.

Если занят чем-то другим - найти процесс:

```bash
sudo ss -tlnp | grep ':80\|:443'
```

---

### `nginx -t: host not found in upstream`

Исторически (до текущей версии) nginx -t падал с ошибкой `host not found in upstream "backend"` потому что прямые `proxy_pass http://backend:5073` требовали работающих контейнеров во время валидации конфига.

**Текущее поведение**: nginx.conf использует variable-based proxy_pass (`set $upstream_xxx service:port; proxy_pass http://$upstream_xxx`), поэтому DNS разрешается в момент запроса, а не при старте. `nginx -t` работает без запущенных upstream-сервисов.

Если ошибка всё же появилась — значит конфиг был сгенерирован старой версией шаблона. Перегенерировать:

```bash
python3 scripts/generate-config.py --env dev --domain yuviron.com --apps client,admin,backoffice
docker compose ... restart nginx
```

---

### `nginx config invalid`

Проверить конфиг внутри работающего контейнера:

```bash
docker exec yuviron-dev-nginx nginx -t
```

Если контейнер не запущен - посмотреть сгенерированный файл:

```bash
cat generated/dev/nginx.conf
```

После исправления источника ошибки перегенерировать и перезапустить:

```bash
python3 scripts/init.py --env dev --domain yuviron.com --no-up
./scripts/cli.py stack down dev && ./scripts/cli.py stack up dev
```

---

### `htpasswd not found`

Файл `generated/<env>/htpasswd` отсутствует. Перегенерировать конфиг:

```bash
python3 scripts/init.py --env dev --domain yuviron.com --no-up
```

---

### Weak/default secrets (strict-режим)

Preflight с `--strict` блокируется при слабых секретах. Для prod это `ERROR`.

Признаки слабого секрета: пустое значение, известные дефолты (`admin`, `password`, `root`, `secret`, `test`, `yuviron`), шаблонные значения вроде `strong_password_1234`, слишком короткие значения для production password/pass, менее 5 уникальных символов (`aaaabbbb`).

Обновить значения в `env/<env>.env`, затем:

```bash
python3 scripts/init.py --env prod --domain yuviron.com --no-up
./scripts/cli.py stack preflight prod --strict
```

---

## Ошибки smoke

### `Service unhealthy: nginx`

1. Логи nginx: `docker logs yuviron-dev-nginx`
2. Конфиг: `docker exec yuviron-dev-nginx nginx -t`
3. Наличие сертификатов: `ls certs/`

---

### `HTTPS check failed for dev.yuviron.com`

* DNS не резолвится -> `nslookup dev.yuviron.com`
* Неверный сертификат -> перевыпустить и применить:

```bash
./scripts/cli.py certs generate --provider mkcert --env dev --domain yuviron.com
./scripts/cli.py certs reload --env dev
```

* nginx не слушает HTTPS -> `docker exec yuviron-dev-nginx nginx -t`

---

### `Backend not ready`

```bash
docker logs yuviron-dev-backend
```

Типичные причины: не выполнены миграции, MySQL не поднялся, неверная connection string.

Принудительно запустить миграции:

```bash
./scripts/cli.py stack migrate dev
```

---

## Контейнер не поднимается

1. Посмотреть логи: `docker logs <container>`
2. Посмотреть detached состояние: `docker inspect <container> | grep -A5 Status`
3. Проверить место на диске: `df -h`
4. Проверить права на storage:

```bash
ls -la $(grep STORAGE_PATH generated/dev/deploy.env | cut -d= -f2)
ls -la $(grep SEQ_STORAGE_PATH generated/dev/deploy.env | cut -d= -f2)
```

5. Проверить наличие Docker сети: `docker network ls | grep yuviron_shared`

---

## DNS не работает внутри VM

### Симптом: `curl: (6) Could not resolve host`

Tailscale MagicDNS перехватил системный DNS resolver. Проверить:

```bash
cat /etc/resolv.conf
```

Если в файле есть `nameserver 100.100.100.100` - MagicDNS активен. Отключить:

```bash
sudo tailscale up --accept-dns=false --ssh
```

После этого VM снова использует обычный системный DNS. Tailscale connectivity и SSH при этом сохраняются.

`doctor dev` также обнаруживает эту проблему: если `100.100.100.100` найден в resolv.conf, команда выводит предупреждение и инструкцию.

Подробнее: [networking.md](networking.md).

---

## Проблемы с Basic Auth

### Где найти credentials

```bash
cat generated/dev/htpasswd.credentials
```

### Сменить пароль

```bash
./scripts/cli.py tools rotate-htpasswd dev
```

nginx перечитывает htpasswd при каждом аутентифицированном запросе - перезапуск контейнера не нужен.

Подробнее обо всех паролях: [passwords.md](passwords.md).

---

## Проблемы с сертификатами

### Браузер показывает ошибку сертификата (dev)

1. Проверить, что `rootCA.crt` установлен в хранилище доверенных корневых CA
2. Проверить, покрывает ли сертификат нужные хосты:

```bash
openssl x509 -in certs/dev-yuviron.com.pem -text -noout | grep -A5 "Subject Alternative Name"
```

3. Перевыпустить и применить:

```bash
./scripts/cli.py certs generate --provider mkcert --env dev --domain yuviron.com
./scripts/cli.py certs reload --env dev
```

### Let's Encrypt: ACME challenge не проходит

1. Убедиться, что nginx запущен и публичный порт 80 попадает в него
2. Проверить доступность: `curl http://<domain>/.well-known/acme-challenge/test`
3. Убедиться, что DNS всех hosts из `routes.env` указывает на сервер

Пропустить self-check при проблемах с loopback:

```bash
./scripts/cli.py certs generate --provider letsencrypt --env prod --domain yuviron.com --skip-public-check
```

---

## Stale config после git pull

Если `git pull` обновил source-файлы (`env/common.env`, `config/routes.yml` и т.п.), а `generated/<env>/` устарела - preflight покажет `source is stale or modified`.

Для dev это исправляется автоматически при следующем `preflight`. Для prod нужен явный `--allow-regenerate` или ручная перегенерация.

---

## Очистка и сброс

### Hard reset dev

```bash
./scripts/cli.py stack down dev
./scripts/cli.py tools docker-clean --mode safe
./scripts/cli.py stack up dev
```

### Полная очистка Docker (осторожно)

```bash
./scripts/cli.py tools docker-clean --mode deep
```

Deep mode удаляет все unused images и build cache. Пересборки после будут медленнее. Не запускать во время активной сборки.

---

## Полезные команды

```bash
# Tailscale
tailscale status
tailscale ip

# Полный статус стека через compose
docker compose \
  --env-file ./generated/dev/deploy.env \
  -f ./infra/compose.yml \
  -f ./generated/dev/compose.frontends.yml \
  -p yuviron-dev \
  ps

# Ручная проверка маршрута
curl -sk --resolve dev-api.yuviron.com:443:127.0.0.1 \
  https://dev-api.yuviron.com/health/ready

# Проверить итоговый env
grep -E '^(ASPNETCORE_ENVIRONMENT|MYSQL_ROOT_PASSWORD)=' generated/dev/deploy.env
```