"""Tests for stack restart/up reliability improvements.

Covers two separate fixes:
1. stack restart --rebuild regenerates swagger specs when missing from disk
   (rollback scenario: git clean -fdx removes packages/api/openapi/*.json)

2. stack up --no-build restarts running-but-unhealthy containers before
   bringing the stack up (Docker restart policy only triggers on container
   exit, not on a failing healthcheck)
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from commands.stack._common import FRONTEND_SWAGGER_DIR, SWAGGER_DOCUMENTS
from commands.stack._up import cmd_restart


def _make_args(
    services: list[str],
    *,
    rebuild: bool = False,
    skip_swagger: bool = False,
    environment: str = "dev",
    project_root: str | None = None,
) -> MagicMock:
    args = MagicMock()
    args.services = services
    args.rebuild = rebuild
    args.skip_swagger = skip_swagger
    args.environment = environment
    args.project_root = project_root
    return args


class RestartSwaggerRegenerationTests(unittest.TestCase):
    """cmd_restart --rebuild regenerates swagger specs when they are missing."""

    def _run(
        self,
        root_dir: Path,
        *,
        specs_present: bool,
        skip_swagger: bool = False,
    ) -> MagicMock:
        swagger_out = root_dir / FRONTEND_SWAGGER_DIR
        if specs_present:
            swagger_out.mkdir(parents=True, exist_ok=True)
            for name in SWAGGER_DOCUMENTS:
                (swagger_out / f"{name}.swagger.json").write_text(
                    '{"openapi":"3.0.0"}', encoding="utf-8"
                )

        mock_swagger = MagicMock()
        mock_run_compose = MagicMock()
        mock_health = MagicMock()
        mock_context = MagicMock()

        with (
            patch("commands.stack._up.resolve_root_dir", return_value=root_dir),
            patch("commands.stack._up.create_compose_context", return_value=mock_context),
            patch("commands.stack._up._prepare_frontend_swagger", mock_swagger),
            patch("commands.stack._up.run_compose", mock_run_compose),
            patch("commands.stack._up._wait_for_service_health", mock_health),
        ):
            cmd_restart(
                _make_args(
                    ["client-app"],
                    rebuild=True,
                    skip_swagger=skip_swagger,
                )
            )

        return mock_swagger

    def test_swagger_regenerated_when_specs_missing(self) -> None:
        """Missing swagger specs trigger _prepare_frontend_swagger before docker build."""
        with tempfile.TemporaryDirectory() as tmp:
            mock_swagger = self._run(Path(tmp), specs_present=False)
        mock_swagger.assert_called_once()

    def test_swagger_not_regenerated_when_specs_present(self) -> None:
        """Existing swagger specs are not regenerated — avoids spinning up prebuild stack."""
        with tempfile.TemporaryDirectory() as tmp:
            mock_swagger = self._run(Path(tmp), specs_present=True)
        mock_swagger.assert_not_called()

    def test_skip_swagger_suppresses_regeneration_even_when_specs_missing(self) -> None:
        """--skip-swagger bypasses regeneration; callers that pre-generated specs use this."""
        with tempfile.TemporaryDirectory() as tmp:
            mock_swagger = self._run(Path(tmp), specs_present=False, skip_swagger=True)
        mock_swagger.assert_not_called()

    def test_no_rebuild_never_triggers_swagger(self) -> None:
        """Without --rebuild, swagger regeneration is never triggered."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mock_swagger = MagicMock()
            with (
                patch("commands.stack._up.resolve_root_dir", return_value=root),
                patch("commands.stack._up.create_compose_context", return_value=MagicMock()),
                patch("commands.stack._up._prepare_frontend_swagger", mock_swagger),
                patch("commands.stack._up.run_compose", MagicMock()),
                patch("commands.stack._up._wait_for_service_health", MagicMock()),
            ):
                cmd_restart(_make_args(["nginx"], rebuild=False))
        mock_swagger.assert_not_called()


class RestartUnhealthyServicesTests(unittest.TestCase):
    """_restart_unhealthy_services() restarts running-but-unhealthy containers.

    Docker's restart_policy triggers only on container exit, never on an
    unhealthy healthcheck. Without explicit restart, a permanently-unhealthy
    container stays broken indefinitely — blocking deploy pipelines that
    poll for healthy status.
    """

    def _run_restart_unhealthy(
        self,
        ps_output: str,
        inspect_outputs: dict[str, str],
    ) -> list[str]:
        """Run _restart_unhealthy_services and return container names passed to docker restart."""
        from commands.stack._up import _restart_unhealthy_services

        mock_context = MagicMock()
        restarted: list[str] = []

        def fake_run_compose(ctx, *args, **kw):
            result = MagicMock()
            result.stdout = ps_output
            return result

        def fake_run(cmd, **kw):
            result = MagicMock()
            if cmd[0] == "docker" and cmd[1] == "inspect":
                cid = cmd[-1]
                result.stdout = inspect_outputs.get(cid, "")
            elif cmd[0] == "docker" and cmd[1] == "restart":
                restarted.extend(cmd[2:])
            return result

        with (
            patch("commands.stack._up.run_compose", fake_run_compose),
            patch("commands.stack._up.run", fake_run),
        ):
            _restart_unhealthy_services(mock_context)

        return restarted

    def test_unhealthy_container_is_restarted(self) -> None:
        """A running container with health=unhealthy must be restarted."""
        restarted = self._run_restart_unhealthy(
            ps_output="abc123",
            inspect_outputs={"abc123": "yuviron-dev-backend/true/unhealthy"},
        )
        self.assertIn("yuviron-dev-backend", restarted)

    def test_healthy_container_is_not_restarted(self) -> None:
        """A running healthy container must not be touched."""
        restarted = self._run_restart_unhealthy(
            ps_output="abc123",
            inspect_outputs={"abc123": "yuviron-dev-backend/true/healthy"},
        )
        self.assertEqual([], restarted)

    def test_stopped_container_is_not_restarted(self) -> None:
        """A stopped (running=false) container is left for compose up to handle."""
        restarted = self._run_restart_unhealthy(
            ps_output="abc123",
            inspect_outputs={"abc123": "yuviron-dev-backend/false/unhealthy"},
        )
        self.assertEqual([], restarted)

    def test_empty_project_does_nothing(self) -> None:
        """When compose ps returns nothing, no docker calls are made."""
        restarted = self._run_restart_unhealthy(ps_output="", inspect_outputs={})
        self.assertEqual([], restarted)

    def test_only_unhealthy_restarted_in_mixed_stack(self) -> None:
        """Only the unhealthy containers are restarted; healthy ones are left alone."""
        restarted = self._run_restart_unhealthy(
            ps_output="id1\nid2\nid3",
            inspect_outputs={
                "id1": "backend/true/unhealthy",
                "id2": "redis/true/healthy",
                "id3": "nginx/true/unhealthy",
            },
        )
        self.assertIn("backend", restarted)
        self.assertIn("nginx", restarted)
        self.assertNotIn("redis", restarted)
