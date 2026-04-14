FROM mcr.microsoft.com/dotnet/sdk:9.0 AS build
WORKDIR /src

COPY src/yuviron-backend ./
RUN dotnet restore src/Yuviron.MediaWorker/Yuviron.MediaWorker.csproj
RUN dotnet publish src/Yuviron.MediaWorker/Yuviron.MediaWorker.csproj \
    -c Release \
    -o /app/publish \
    /p:UseAppHost=false

FROM mcr.microsoft.com/dotnet/aspnet:9.0 AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build /app/publish ./

ENTRYPOINT ["dotnet", "Yuviron.MediaWorker.dll"]