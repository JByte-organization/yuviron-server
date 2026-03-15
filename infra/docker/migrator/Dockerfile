FROM mcr.microsoft.com/dotnet/sdk:9.0 AS build

WORKDIR /src

COPY src/yuviron-backend .

RUN dotnet tool install --global dotnet-ef --version 9.*
ENV PATH="${PATH}:/root/.dotnet/tools"

RUN dotnet restore src/Yuviron.Api/Yuviron.Api.csproj
RUN dotnet build src/Yuviron.Api/Yuviron.Api.csproj -c Release

CMD ["dotnet", "ef", "database", "update", "-p", "src/Yuviron.Infrastructure", "-s", "src/Yuviron.Api"]