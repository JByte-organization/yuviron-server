"""Tests for stack restart --rebuild swagger-spec pre-generation.

During rollback, `git clean -fdx` removes the swagger spec files from disk.
`stack restart --rebuild` must detect missing specs and regenerate them
before the Docker build, otherwise `pnpm api:gen` inside the Dockerfile
tries to fetch specs from live URLs (which fail when the backend is down).
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
