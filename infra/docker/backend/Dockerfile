FROM mcr.microsoft.com/dotnet/sdk:9.0 AS build

WORKDIR /src

COPY src/yuviron-backend .

RUN dotnet restore src/Yuviron.Api/Yuviron.Api.csproj

RUN dotnet publish src/Yuviron.Api/Yuviron.Api.csproj \
    -c Release \
    -o /app/publish \
    /p:UseAppHost=false


FROM mcr.microsoft.com/dotnet/aspnet:9.0

RUN apt-get update && apt-get install -y wget && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=build /app/publish .

ENV ASPNETCORE_URLS=http://+:5073

EXPOSE 5073

ENTRYPOINT ["dotnet","Yuviron.Api.dll"]
