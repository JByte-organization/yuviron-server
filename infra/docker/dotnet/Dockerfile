# syntax=docker/dockerfile:1

ARG DOTNET_VERSION=9.0

FROM mcr.microsoft.com/dotnet/sdk:${DOTNET_VERSION} AS source

WORKDIR /src

COPY src/yuviron-backend/src/Yuviron.Api/Yuviron.Api.csproj ./src/Yuviron.Api/
COPY src/yuviron-backend/src/Yuviron.Application/Yuviron.Application.csproj ./src/Yuviron.Application/
COPY src/yuviron-backend/src/Yuviron.Domain/Yuviron.Domain.csproj ./src/Yuviron.Domain/
COPY src/yuviron-backend/src/Yuviron.Infrastructure/Yuviron.Infrastructure.csproj ./src/Yuviron.Infrastructure/
COPY src/yuviron-backend/src/Yuviron.MediaWorker/Yuviron.MediaWorker.csproj ./src/Yuviron.MediaWorker/

FROM source AS restore

ARG APP_PROJECT=src/Yuviron.Api/Yuviron.Api.csproj

RUN --mount=type=cache,id=nuget,target=/root/.nuget/packages,sharing=locked \
    dotnet restore ./${APP_PROJECT}

COPY src/yuviron-backend/ ./

FROM restore AS publish

ARG APP_PROJECT=src/Yuviron.Api/Yuviron.Api.csproj

RUN --mount=type=cache,id=nuget,target=/root/.nuget/packages,sharing=locked \
    dotnet publish ./${APP_PROJECT} \
    -c Release \
    -o /app/publish \
    --no-restore \
    /p:UseAppHost=false

FROM restore AS migrator

ARG DOTNET_APP_UID=10001
ARG DOTNET_APP_GID=10001
ARG MIGRATOR_STARTUP_PROJECT=src/Yuviron.Api/Yuviron.Api.csproj
ARG MIGRATOR_CONFIGURATION=Release

RUN --mount=type=cache,id=nuget,target=/root/.nuget/packages,sharing=locked \
    dotnet tool install --tool-path /usr/local/dotnet-tools dotnet-ef --version 9.*

ENV PATH="${PATH}:/usr/local/dotnet-tools"
ENV DOTNET_NOLOGO=1
ENV DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1

RUN --mount=type=cache,id=nuget,target=/root/.nuget/packages,sharing=locked \
    dotnet build ./${MIGRATOR_STARTUP_PROJECT} -c ${MIGRATOR_CONFIGURATION} --no-restore

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
ARG RUNTIME_PACKAGES=""

RUN set -eux; \
    if [ -n "${RUNTIME_PACKAGES}" ]; then \
        apt-get update; \
        apt-get install -y --no-install-recommends ${RUNTIME_PACKAGES}; \
        rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app

ENV HOME=/tmp
ENV DOTNET_BUNDLE_EXTRACT_BASE_DIR=/tmp

FROM runtime-base AS backend

ARG DOTNET_APP_UID=10001
ARG DOTNET_APP_GID=10001

COPY --chown=${DOTNET_APP_UID}:${DOTNET_APP_GID} --from=publish /app/publish ./

USER ${DOTNET_APP_UID}:${DOTNET_APP_GID}
ENTRYPOINT ["dotnet", "Yuviron.Api.dll"]

FROM runtime-base AS media-worker

ARG DOTNET_APP_UID=10001
ARG DOTNET_APP_GID=10001

COPY --chown=${DOTNET_APP_UID}:${DOTNET_APP_GID} --from=publish /app/publish ./

USER ${DOTNET_APP_UID}:${DOTNET_APP_GID}
ENTRYPOINT ["dotnet", "Yuviron.MediaWorker.dll"]
