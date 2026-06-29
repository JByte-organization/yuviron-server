# =============================================================================
# infra/docker/dotnet/Dockerfile — Multi-stage Dockerfile для .NET-сервисов.
#
# Собирает три финальных образа из одного файла:
#
#   backend      — Yuviron.Api (.NET 9 ASP.NET API, порт 5073)
#                  HEALTHCHECK: GET /health/ready
#
#   media-worker — Yuviron.MediaWorker (фоновый обработчик медиа, порт 5074)
#                  HEALTHCHECK: GET /health/ready
#
#   migrator     — EF Core migrator (запускается как --rm контейнер)
#                  CMD: /usr/local/bin/yuviron-migrator (migrator.sh)
#
# Стадии сборки:
#   dotnet-ef-tool — устанавливает dotnet-ef CLI; не зависит от исходников,
#                  поэтому кэшируется до смены версии SDK/тула, а не до смены кода
#   source       — копирует только .csproj файлы (для кэша NuGet restore)
#   restore      — dotnet restore + копирует весь исходный код
#   build        — dotnet build Yuviron.Api + Yuviron.MediaWorker один раз;
#                  publish-api/publish-worker/migrator наследуют от неё, чтобы
#                  не пересобирать общий граф Domain/Application/Infrastructure
#                  трижды (раньше отдельный "dotnet build" в migrator дублировал
#                  publish-api и добавлял ~2.5 минуты на каждый деплой)
#   publish-api  — dotnet publish Yuviron.Api --no-build → /app/publish
#   publish-worker — dotnet publish Yuviron.MediaWorker --no-build → /app/publish
#   migrator     — переиспользует build-стадию, добавляет dotnet-ef CLI
#   runtime-base — mcr.microsoft.com/dotnet/aspnet (без SDK) + runtime-packages.txt
#   backend      — runtime-base + артефакты publish-api
#   media-worker — runtime-base + артефакты publish-worker
#
# Безопасность:
#   - Все сервисы запускаются от UID/GID 10001 (не root)
#   - HOME=/tmp (нет доступа к домашней директории)
#   - NuGet кэш через BuildKit mount (не попадает в слои образа)
# =============================================================================
ARG DOTNET_VERSION=9.0

FROM mcr.microsoft.com/dotnet/sdk:${DOTNET_VERSION} AS dotnet-ef-tool

RUN --mount=type=cache,id=nuget,target=/root/.nuget/packages,sharing=locked \
    dotnet tool install --tool-path /usr/local/dotnet-tools dotnet-ef --version 9.*

FROM mcr.microsoft.com/dotnet/sdk:${DOTNET_VERSION} AS source

WORKDIR /src

COPY src/yuviron-backend/src/Yuviron.Api/Yuviron.Api.csproj ./src/Yuviron.Api/
COPY src/yuviron-backend/src/Yuviron.Application/Yuviron.Application.csproj ./src/Yuviron.Application/
COPY src/yuviron-backend/src/Yuviron.Domain/Yuviron.Domain.csproj ./src/Yuviron.Domain/
COPY src/yuviron-backend/src/Yuviron.Infrastructure/Yuviron.Infrastructure.csproj ./src/Yuviron.Infrastructure/
COPY src/yuviron-backend/src/Yuviron.MediaWorker/Yuviron.MediaWorker.csproj ./src/Yuviron.MediaWorker/

FROM source AS restore

RUN --mount=type=cache,id=nuget,target=/root/.nuget/packages,sharing=locked \
    dotnet restore src/Yuviron.Api/Yuviron.Api.csproj \
    && dotnet restore src/Yuviron.MediaWorker/Yuviron.MediaWorker.csproj

COPY src/yuviron-backend/ ./

FROM restore AS build

RUN --mount=type=cache,id=nuget,target=/root/.nuget/packages,sharing=locked \
    dotnet build src/Yuviron.Api/Yuviron.Api.csproj -c Release --no-restore \
    && dotnet build src/Yuviron.MediaWorker/Yuviron.MediaWorker.csproj -c Release --no-restore

FROM build AS publish-api

RUN --mount=type=cache,id=nuget,target=/root/.nuget/packages,sharing=locked \
    dotnet publish src/Yuviron.Api/Yuviron.Api.csproj \
    -c Release \
    -o /app/publish \
    --no-build \
    /p:UseAppHost=false

FROM build AS publish-worker

RUN --mount=type=cache,id=nuget,target=/root/.nuget/packages,sharing=locked \
    dotnet publish src/Yuviron.MediaWorker/Yuviron.MediaWorker.csproj \
    -c Release \
    -o /app/publish \
    --no-build \
    /p:UseAppHost=false

FROM build AS migrator

ARG DOTNET_APP_UID=10001
ARG DOTNET_APP_GID=10001
ARG MIGRATOR_STARTUP_PROJECT=src/Yuviron.Api/Yuviron.Api.csproj
ARG MIGRATOR_CONFIGURATION=Release

COPY --from=dotnet-ef-tool /usr/local/dotnet-tools /usr/local/dotnet-tools

ENV PATH="${PATH}:/usr/local/dotnet-tools"
ENV DOTNET_NOLOGO=1
ENV DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1

COPY infra/docker/dotnet/migrator.sh /usr/local/bin/yuviron-migrator

RUN chmod -R a+rX /src /usr/local/dotnet-tools \
    && chmod 755 /usr/local/bin/yuviron-migrator

ENV HOME=/tmp
ENV DOTNET_CLI_HOME=/tmp
ENV MIGRATOR_STARTUP_PROJECT=${MIGRATOR_STARTUP_PROJECT}
ENV MIGRATOR_CONFIGURATION=${MIGRATOR_CONFIGURATION}

USER ${DOTNET_APP_UID}:${DOTNET_APP_GID}
CMD ["/usr/local/bin/yuviron-migrator"]

FROM mcr.microsoft.com/dotnet/aspnet:${DOTNET_VERSION} AS runtime-base

ARG DOTNET_APP_UID=10001
ARG DOTNET_APP_GID=10001

USER root

COPY infra/docker/dotnet/runtime-packages.txt /tmp/runtime-packages.txt

RUN set -eux; \
    apt-get update; \
    xargs -r apt-get install -y --no-install-recommends < /tmp/runtime-packages.txt; \
    rm -rf /var/lib/apt/lists/* /tmp/runtime-packages.txt

WORKDIR /app

ENV HOME=/tmp
ENV DOTNET_BUNDLE_EXTRACT_BASE_DIR=/tmp

FROM runtime-base AS backend

ARG DOTNET_APP_UID=10001
ARG DOTNET_APP_GID=10001

COPY --chown=${DOTNET_APP_UID}:${DOTNET_APP_GID} --from=publish-api /app/publish ./

ENV ASPNETCORE_HTTP_PORTS=5073
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=10 \
    CMD wget -qO- http://127.0.0.1:5073/health/ready >/dev/null || exit 1

USER ${DOTNET_APP_UID}:${DOTNET_APP_GID}
ENTRYPOINT ["dotnet", "Yuviron.Api.dll"]

FROM runtime-base AS media-worker

ARG DOTNET_APP_UID=10001
ARG DOTNET_APP_GID=10001

COPY --chown=${DOTNET_APP_UID}:${DOTNET_APP_GID} --from=publish-worker /app/publish ./

ENV ASPNETCORE_HTTP_PORTS=5074
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=10 \
    CMD wget -qO- http://127.0.0.1:5074/health/ready >/dev/null || exit 1

USER ${DOTNET_APP_UID}:${DOTNET_APP_GID}
ENTRYPOINT ["dotnet", "Yuviron.MediaWorker.dll"]
