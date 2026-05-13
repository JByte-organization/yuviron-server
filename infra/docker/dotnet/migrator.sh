#!/bin/sh
set -eu

EF_PROJECT="src/Yuviron.Infrastructure/Yuviron.Infrastructure.csproj"
EF_STARTUP_PROJECT="${MIGRATOR_STARTUP_PROJECT:-src/Yuviron.Api/Yuviron.Api.csproj}"
EF_CONFIGURATION="${MIGRATOR_CONFIGURATION:-Release}"
EF_OBJ_DIR="/tmp/ef-obj"

mkdir -p "${EF_OBJ_DIR}"
rm -rf "${EF_OBJ_DIR:?}"/* "${EF_OBJ_DIR}"/.[!.]* "${EF_OBJ_DIR}"/..?* 2>/dev/null || true
cp -R /src/src/Yuviron.Infrastructure/obj/. "${EF_OBJ_DIR}/"

EF_ARGS="
  --project ${EF_PROJECT}
  --startup-project ${EF_STARTUP_PROJECT}
  --configuration ${EF_CONFIGURATION}
  --no-build
  --msbuildprojectextensionspath ${EF_OBJ_DIR}
"

export MSBuildProjectExtensionsPath="${EF_OBJ_DIR}/"

echo "Applying EF Core migrations"
dotnet ef database update ${EF_ARGS}

echo "Verifying EF Core migration state"
migrations_output="$(dotnet ef migrations list ${EF_ARGS})"
printf '%s\n' "${migrations_output}"

if printf '%s\n' "${migrations_output}" | grep -E "(\[Pending\]|\(Pending\)|Pending)" >/dev/null; then
    echo "EF Core verification failed: pending migrations remain after database update" >&2
    exit 1
fi

echo "EF Core migration verification completed"
