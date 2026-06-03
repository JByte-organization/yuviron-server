# =============================================================================
# infra/edge/Dockerfile — Образ nginx для edge-прокси (reverse proxy + CDN).
#
# На основе официального nginx:alpine (pinned SHA256 для воспроизводимости).
#
# Что делает при сборке:
#   1. Обновляет nghttp2-libs и libxml2 (закрытие CVE в alpine базовых пакетах)
#   2. Устанавливает gettext (envsubst — подстановка переменных в конфигах)
#      и openssl (для проверки сертификатов)
#   3. Создаёт системную группу и пользователя app (UID/GID 10001)
#      — nginx worker процессы запускаются от непривилегированного пользователя
#   4. Создаёт папку для CDN-кэша медиа-файлов и задаёт права
#
# Безопасность:
#   - Запускается от UID 10001 (не root)
#   - Кэш медиа-файлов только для этого пользователя
# =============================================================================
FROM nginx:alpine@sha256:2f07d83bf561b506400dc183b1b2003803e39efbd22451f848adaba14d28c7c7
RUN apk upgrade --no-cache nghttp2-libs libxml2 \
    && apk add --no-cache gettext openssl \
    && addgroup -S -g 10001 app \
    && adduser -S -D -H -u 10001 -G app app \
    && mkdir -p /var/cache/nginx/media \
    && chown -R 10001:10001 /var/cache/nginx/media

USER 10001:10001
