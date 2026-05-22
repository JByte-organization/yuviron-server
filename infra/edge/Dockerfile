FROM nginx:alpine@sha256:2f07d83bf561b506400dc183b1b2003803e39efbd22451f848adaba14d28c7c7
RUN apk upgrade --no-cache nghttp2-libs \
    && apk add --no-cache gettext openssl \
    && addgroup -S -g 10001 app \
    && adduser -S -D -H -u 10001 -G app app \
    && mkdir -p /var/cache/nginx/yuviron_media \
    && chown -R 10001:10001 /var/cache/nginx/yuviron_media

USER 10001:10001
