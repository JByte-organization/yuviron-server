# syntax=docker/dockerfile:1

FROM busybox:1.37.0-musl AS healthcheck

FROM mcr.microsoft.com/dotnet/aspire-dashboard:9.0

COPY --from=healthcheck /bin/busybox /usr/local/bin/busybox

USER app
