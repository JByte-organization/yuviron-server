FROM nginx:alpine
RUN apk add --no-cache gettext openssl \
    && addgroup -S -g 10001 app \
    && adduser -S -D -H -u 10001 -G app app \
    && mkdir -p /var/cache/nginx/yuviron_media \
    && chown -R 10001:10001 /var/cache/nginx/yuviron_media

USER 10001:10001
ENTRYPOINT []
