from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
ROOT_DIR = SCRIPTS_ROOT.parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from core.env import parse_env_file
from core.paths import resolve_runtime_path


RUN_E2E_ENV = "YUVIRON_RUN_DOCKER_E2E"

DEV_ENV_FIXTURE = """\
MYSQL_ROOT_PASSWORD=e2e-root-password-1234567890
MYSQL_DATABASE=yuviron_e2e
MYSQL_USER=yuviron_e2e
MYSQL_PASSWORD=e2e-db-password-1234567890

ASPNETCORE_ENVIRONMENT=Development
ConnectionStrings__Default=server=mysql;port=3306;database=yuviron_e2e;user=yuviron_e2e;password=e2e-db-password-1234567890;
REDIS_PASSWORD=e2e-redis-password-1234567890
ConnectionStrings__Redis=redis:6379,password=e2e-redis-password-1234567890
Swagger__Enabled=true

FILE_STORAGE_ROOT=/var/yuviron-server/storage

SEQ_FIRSTRUN_ADMINUSERNAME=admin
SEQ_FIRSTRUN_ADMINPASSWORDHASH=e2e-seq-password-hash-placeholder
Seq__ServerUrl=http://seq:5341

ASPIRE_ASPNETCORE_URLS=http://0.0.0.0:18888
ASPIRE_OTLP_ENDPOINT_URL=http://0.0.0.0:18889
ASPIRE_FRONTEND_AUTH_MODE=BrowserToken
ASPIRE_FRONTEND_BROWSER_TOKEN=e2e-browser-token-1234567890abcdef
ASPIRE_OTLP_API_KEY=e2e-otlp-api-key-1234567890abcdef

OTEL_EXPORTER_OTLP_PROTOCOL=grpc
OTEL_EXPORTER_OTLP_ENDPOINT=http://aspire-dashboard:18889
OTEL_EXPORTER_OTLP_HEADERS=x-otlp-api-key=${ASPIRE_OTLP_API_KEY}

RABBITMQ_DEFAULT_USER=yuviron_e2e
RABBITMQ_DEFAULT_PASS=e2e-rabbit-password-1234567890
RABBITMQ_DEFAULT_VHOST=/yuviron_e2e
RabbitMQ__Host=rabbitmq
RabbitMQ__Port=5672
RabbitMQ__Username=yuviron_e2e
RabbitMQ__VirtualHost=/yuviron_e2e

JamendoApi__ClientId=e2e-jamendo-client
"""


def _skip_unless_enabled() -> None:
    if os.getenv(RUN_E2E_ENV) != "1":
        raise unittest.SkipTest(f"set {RUN_E2E_ENV}=1 to run Docker dry-run e2e")

    docker = shutil.which("docker")
    if docker is None:
        raise unittest.SkipTest("docker CLI is not installed")

    info = subprocess.run([docker, "info"], capture_output=True, text=True, check=False)
    if info.returncode != 0:
        raise unittest.SkipTest("docker daemon is not available")

    compose_help = subprocess.run([docker, "compose", "--help"], capture_output=True, text=True, check=False)
    if compose_help.returncode != 0 or "--dry-run" not in (compose_help.stdout or ""):
        raise unittest.SkipTest("docker compose --dry-run is not available")


def _copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache"),
    )


def _build_e2e_project(root: Path, project_name: str) -> None:
    _copy_tree(ROOT_DIR / "scripts", root / "scripts")
    _copy_tree(ROOT_DIR / "config", root / "config")
    _copy_tree(ROOT_DIR / "infra", root / "infra")

    env_dir = root / "env"
    env_dir.mkdir(parents=True)
    shutil.copy2(ROOT_DIR / "env" / "common.env", env_dir / "common.env")
    shutil.copy2(ROOT_DIR / "env" / "schema.json", env_dir / "schema.json")
    (env_dir / "dev.env").write_text(DEV_ENV_FIXTURE, encoding="utf-8")

    (root / "config" / "project.yml").write_text(f"project_name: {project_name}\n", encoding="utf-8")
    frontend_root = root / "src" / "yuviron-frontend"
    frontend_root.mkdir(parents=True)
    (frontend_root / "package.json").write_text('{"scripts":{"build":"next build"}}\n', encoding="utf-8")


class StackE2EDryRunTests(unittest.TestCase):
    def test_init_preflight_and_up_dry_run_cycle(self) -> None:
        _skip_unless_enabled()

        project_name = f"yuviron-e2e-{os.getpid()}"
        shared_network = f"{project_name}_shared"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            _build_e2e_project(root, project_name)

            try:
                self._run(
                    root,
                    [
                        sys.executable,
                        str(root / "scripts" / "init.py"),
                        "--env",
                        "dev",
                        "--domain",
                        "example.com",
                        "--apps",
                        "admin,backoffice",
                        "--routes",
                        "-1",
                        "--no-certs",
                        "--no-preflight",
                        "--no-up",
                    ],
                )

                self._write_dummy_tls_files(root)

                self._run(
                    root,
                    [
                        sys.executable,
                        str(root / "scripts" / "cli.py"),
                        "stack",
                        "preflight",
                        "--dry-run",
                        "--no-header",
                        "dev",
                        str(root),
                    ],
                    timeout=180,
                )

                up = self._run(
                    root,
                    [
                        sys.executable,
                        str(root / "scripts" / "cli.py"),
                        "stack",
                        "up",
                        "--dry-run",
                        "dev",
                        str(root),
                    ],
                    timeout=180,
                )
            finally:
                subprocess.run(["docker", "network", "rm", shared_network], capture_output=True, check=False)

        self.assertIn("dry-run", (up.stdout + up.stderr).lower())

    def _write_dummy_tls_files(self, root: Path) -> None:
        deploy_env = parse_env_file(root / "generated" / "dev" / "deploy.env")
        for key in ("CERT_FILE", "KEY_FILE"):
            path = resolve_runtime_path(root, deploy_env[key])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("dummy test certificate file\n", encoding="utf-8")

    def _run(
        self,
        root: Path,
        cmd: list[str],
        *,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            cmd,
            cwd=root,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            self.fail(
                "Command failed:\n"
                f"{' '.join(cmd)}\n\n"
                f"stdout:\n{result.stdout}\n\n"
                f"stderr:\n{result.stderr}"
            )
        return result


if __name__ == "__main__":
    unittest.main()
