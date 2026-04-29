# syntax=docker/dockerfile:1

FROM mcr.microsoft.com/dotnet/sdk:9.0

WORKDIR /src

COPY src/yuviron-backend/src/Yuviron.Api/Yuviron.Api.csproj ./src/Yuviron.Api/
COPY src/yuviron-backend/src/Yuviron.Application/Yuviron.Application.csproj ./src/Yuviron.Application/
COPY src/yuviron-backend/src/Yuviron.Domain/Yuviron.Domain.csproj ./src/Yuviron.Domain/
COPY src/yuviron-backend/src/Yuviron.Infrastructure/Yuviron.Infrastructure.csproj ./src/Yuviron.Infrastructure/
COPY src/yuviron-backend/src/Yuviron.MediaWorker/Yuviron.MediaWorker.csproj ./src/Yuviron.MediaWorker/

RUN dotnet tool install --global dotnet-ef --version 9.*
ENV PATH="${PATH}:/root/.dotnet/tools"
ENV DOTNET_NOLOGO=1
ENV DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1

RUN dotnet restore ./src/Yuviron.Api/Yuviron.Api.csproj

COPY src/yuviron-backend/ ./

RUN dotnet build ./src/Yuviron.Api/Yuviron.Api.csproj -c Release

CMD ["dotnet", "ef", "database", "update", "--project", "src/Yuviron.Infrastructure/Yuviron.Infrastructure.csproj", "--startup-project", "src/Yuviron.Api/Yuviron.Api.csproj"]