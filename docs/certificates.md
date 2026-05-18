# Сертификаты

Генерация сертификатов, mkcert, Let's Encrypt, rootCA и установка сертификатов разработчиками.

[← К README](../README.md)

## 🔐 CERTS

### Генерация

```bash
./scripts/cli.py certs generate --provider mkcert --env dev --domain yuviron.com
./scripts/cli.py certs generate --provider letsencrypt --env prod --domain yuviron.com --email ops@yuviron.com
./scripts/cli.py certs renew --env prod --domain yuviron.com
```

---

## Сертификаты

Сертификаты хранятся в директории:

```text
certs/
```

Для dev и prod могут использоваться разные сертификаты и разный режим привязки сертификатов к маршрутам.

`infra/compose.yml` монтирует весь каталог `certs/` в nginx как `/etc/nginx/certs`. Конкретные файлы выбираются в сгенерированном `generated/<env>/nginx.conf`:

* `NGINX_CERT_MODE=shared` — один общий SAN/wildcard certificate для всех route hosts.
* `NGINX_CERT_MODE=per-route` — каждый route host получает отдельную пару cert/key через nginx `map $ssl_server_name ...`.

По умолчанию `dev` генерируется в режиме `shared`, `prod` — в режиме `per-route`. Общий cert/key из `CERT_FILE` и `KEY_FILE` остаётся default/fallback сертификатом для default HTTPS server и healthcheck.

Генерация через CLI:

```bash
./scripts/cli.py certs generate --provider mkcert --env dev --domain yuviron.com
./scripts/cli.py certs generate --provider letsencrypt --env prod --domain yuviron.com --email ops@yuviron.com
./scripts/cli.py certs renew --env prod --domain yuviron.com
```

После замены файлов сертификата работающий nginx должен перечитать их:

```bash
./scripts/cli.py certs reload --env dev
./scripts/cli.py certs reload --env prod
```

Команда сначала выполняет `nginx -t` внутри контейнера, затем `nginx -s reload`.
Для автоматической ротации Let's Encrypt запускай `certs renew`; при изменении файлов nginx перечитывается автоматически.

Перед генерацией сертификатов должен существовать `generated/<env>/routes.env`, поэтому сначала запускается `scripts/init.py` или `scripts/generate-config.py`.

Если используется локальный Root CA, его необходимо установить на клиентские машины разработчиков.

---

## Генерация SSL сертификатов через mkcert

CLI использует `mkcert` и SAN-список из `generated/<env>/routes.env`.
Если `--provider` не указан, используется `mkcert`.

```bash
./scripts/cli.py certs generate --provider mkcert --env dev --domain yuviron.com
```

Команда выполняет:

1. проверку mkcert
2. предложение установить `mkcert` и `libnss3-tools`, если mkcert не найден
3. создание/установку Root CA
4. генерацию сертификатов
5. копирование `rootCA.pem` в `~/rootCA.crt`

Если стек уже запущен, после перевыпуска сертификата нужно применить его в nginx:

```bash
./scripts/cli.py certs reload --env dev
```

После выполнения может появиться файл:

```text
~/rootCA.crt
```

В `shared` режиме один сертификат покрывает hosts из `routes.env`, например:

```text
dev.yuviron.com
dev-backoffice.yuviron.com
dev-admin.yuviron.com
dev-api.yuviron.com
dev-seq.yuviron.com
dev-aspire.yuviron.com
dev-i.yuviron.com
```

В `per-route` режиме `mkcert` всё равно выпускает общий SAN-сертификат, а затем раскладывает его копии в per-route пути вида:

```text
certs/<env>/<host>.pem
certs/<env>/<host>-key.pem
```

Это удобно как bootstrap для production nginx перед выпуском настоящих Let's Encrypt сертификатов.

---

## Генерация SSL сертификатов через Let's Encrypt

CLI использует `certbot --webroot` и route hosts из `generated/<env>/routes.env`.
Для ACME HTTP-01 challenge nginx отдаёт файлы из `certs/acme-challenge` по пути `/.well-known/acme-challenge/`.

В `shared` режиме CLI выпускает один SAN-сертификат `certs/<env>-<domain>.pem`.
В `per-route` режиме CLI выпускает отдельный Let's Encrypt certificate для каждого route host и синхронизирует файлы в:

```text
certs/<env>/<host>.pem
certs/<env>/<host>-key.pem
```

```bash
./scripts/cli.py certs generate --provider letsencrypt --env prod --domain yuviron.com --email ops@yuviron.com
```

Email можно не передавать флагом: CLI возьмёт `LETSENCRYPT_EMAIL` или `CERTBOT_EMAIL`, либо спросит интерактивно.

```bash
LETSENCRYPT_EMAIL=ops@yuviron.com ./scripts/cli.py certs generate --provider letsencrypt --env prod --domain yuviron.com
```

Перед выпуском сертификата:

1. установи `certbot`
2. убедись, что DNS всех hosts из `routes.env` указывает на сервер
3. убедись, что публичный порт `80` попадает в nginx
4. перегенерируй nginx config после обновления шаблонов

Для prod `HTTP_PORT` по умолчанию равен `80`. Для dev по умолчанию используется `8080`, поэтому Let's Encrypt сработает только если внешний порт `80` прокинут на этот nginx.

Перед запуском `certbot` CLI проверяет, что сервис `nginx` запущен, и создаёт probe-файл в:

```text
certs/acme-challenge/.well-known/acme-challenge/<token>
```

Затем CLI запрашивает его по публичному HTTP URL каждого host:

```text
http://<host>/.well-known/acme-challenge/<token>
```

На чистом сервере nginx всё равно должен стартовать с каким-либо существующим default cert/key, например временно выпущенным через `mkcert`, потому что webroot challenge обслуживает именно nginx. Для `prod` с дефолтным `NGINX_CERT_MODE=per-route` временный `mkcert` также подготовит per-route файлы, чтобы preflight и HTTPS smoke не падали до замены на Let's Encrypt.

Если инфраструктура не поддерживает такую self-check проверку с самого сервера, её можно пропустить:

```bash
./scripts/cli.py certs generate --provider letsencrypt --env prod --domain yuviron.com --email ops@yuviron.com --skip-public-check
```

По умолчанию CLI использует безопасный режим `--keep-until-expiring`. Для принудительной ротации:

```bash
./scripts/cli.py certs generate --provider letsencrypt --env prod --domain yuviron.com --email ops@yuviron.com --force-renewal
```

После успешного выпуска и обновления файлов nginx автоматически выполняет `nginx -t` и `nginx -s reload`.
Если reload нужно выполнить вручную:

```bash
./scripts/cli.py certs generate --provider letsencrypt --env prod --domain yuviron.com --email ops@yuviron.com --no-reload
./scripts/cli.py certs reload --env prod
```

Продление существующего Let's Encrypt сертификата:

```bash
./scripts/cli.py certs renew --env prod --domain yuviron.com
```

Принудительное продление:

```bash
./scripts/cli.py certs renew --env prod --domain yuviron.com --force-renewal
```

---

## Назначение rootCA.crt

Файл:

```text
rootCA.crt
```

является локальным **Root Certificate Authority**.

Если установить его разработчикам на рабочие машины, браузер будет доверять сертификатам dev-окружения.

HTTPS будет работать без предупреждений безопасности.

---

## Передача сертификатов разработчикам

Если dev-среда работает через локальный Root CA, администратор должен передать разработчикам:

```text
rootCA.crt
radmin_setup.bat
```

или другой набор файлов/инструкций, который используется внутри команды.

---

## Установка сертификата на Windows

1. Открыть `rootCA.crt`
2. Нажать **Install Certificate / Установить сертификат**
3. Выбрать **Local Machine / Локальный компьютер**
4. Выбрать хранилище:

```text
Trusted Root Certification Authorities / Доверенные корневые центры сертификации
```

5. Завершить установку

После этого браузер будет доверять сертификатам, подписанным этим Root CA.

---
