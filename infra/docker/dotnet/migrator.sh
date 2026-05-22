#!/bin/sh
set -eu

EF_PROJECT="src/Yuviron.Infrastructure/Yuviron.Infrastructure.csproj"
EF_STARTUP_PROJECT="${MIGRATOR_STARTUP_PROJECT:-src/Yuviron.Api/Yuviron.Api.csproj}"
EF_CONFIGURATION="${MIGRATOR_CONFIGURATION:-Release}"
EF_OBJ_DIR="/tmp/ef-obj"

case "${ASPNETCORE_ENVIRONMENT:-}" in
    [Pp][Rr][Oo][Dd][Uu][Cc][Tt][Ii][Oo][Nn])
        db_name="${MYSQL_DATABASE:-}"
        fingerprint="${ALLOW_PRODUCTION_MIGRATE:-false}"
        if [ -z "${db_name}" ]; then
            echo "Refusing to run EF Core migrations in Production." >&2
            echo "MYSQL_DATABASE is not set; cannot verify migration fingerprint." >&2
            exit 1
        fi
        if [ "${fingerprint}" != "${db_name}" ]; then
            echo "Refusing to run EF Core migrations in Production." >&2
            echo "ALLOW_PRODUCTION_MIGRATE must equal MYSQL_DATABASE ('${db_name}'), got '${fingerprint}'." >&2
            echo "Set ALLOW_PRODUCTION_MIGRATE=<database-name> for this one-off migrator run." >&2
            exit 1
        fi
        echo "Production migration fingerprint verified: ALLOW_PRODUCTION_MIGRATE=${fingerprint}"
        ;;
esac

mkdir -p "${EF_OBJ_DIR}"
rm -rf "${EF_OBJ_DIR:?}"/* "${EF_OBJ_DIR}"/.[!.]* "${EF_OBJ_DIR}"/..?* 2>/dev/null || true
cp -R /src/src/Yuviron.Infrastructure/obj/. "${EF_OBJ_DIR}/"

export MSBuildProjectExtensionsPath="${EF_OBJ_DIR}/"

run_ef() {
    dotnet ef "$@" \
        --project "${EF_PROJECT}" \
        --startup-project "${EF_STARTUP_PROJECT}" \
        --configuration "${EF_CONFIGURATION}" \
        --no-build \
        --msbuildprojectextensionspath "${EF_OBJ_DIR}"
}

echo "Applying EF Core migrations"
run_ef database update

echo "Verifying EF Core migration state"
migrations_output="$(run_ef migrations list)"
printf '%s\n' "${migrations_output}"

if printf '%s\n' "${migrations_output}" | grep -E "(\[Pending\]|\(Pending\)|Pending)" >/dev/null; then
    echo "EF Core verification failed: pending migrations remain after database update" >&2
    exit 1
fi

echo "EF Core migration verification completed"
