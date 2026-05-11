# syntax=docker/dockerfile:1

FROM mcr.microsoft.com/dotnet/sdk:9.0 AS build

WORKDIR /src

COPY src/yuviron-backend/src/Yuviron.Api/Yuviron.Api.csproj ./src/Yuviron.Api/
COPY src/yuviron-backend/src/Yuviron.Application/Yuviron.Application.csproj ./src/Yuviron.Application/
COPY src/yuviron-backend/src/Yuviron.Domain/Yuviron.Domain.csproj ./src/Yuviron.Domain/
COPY src/yuviron-backend/src/Yuviron.Infrastructure/Yuviron.Infrastructure.csproj ./src/Yuviron.Infrastructure/
COPY src/yuviron-backend/src/Yuviron.MediaWorker/Yuviron.MediaWorker.csproj ./src/Yuviron.MediaWorker/

RUN --mount=type=cache,id=nuget,target=/root/.nuget/packages,sharing=locked \
    dotnet restore ./src/Yuviron.MediaWorker/Yuviron.MediaWorker.csproj

COPY src/yuviron-backend/ ./

RUN --mount=type=cache,id=nuget,target=/root/.nuget/packages,sharing=locked \
    dotnet publish ./src/Yuviron.MediaWorker/Yuviron.MediaWorker.csproj \
    -c Release \
    -o /app/publish \
    --no-restore \
    /p:UseAppHost=false

FROM mcr.microsoft.com/dotnet/aspnet:9.0

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --home-dir /tmp --shell /usr/sbin/nologin --no-create-home app \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --chown=10001:10001 --from=build /app/publish ./

ENV HOME=/tmp
ENV DOTNET_BUNDLE_EXTRACT_BASE_DIR=/tmp

USER 10001:10001
ENTRYPOINT ["dotnet", "Yuviron.MediaWorker.dll"]
