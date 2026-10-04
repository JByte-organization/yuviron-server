from __future__ import annotations

import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from commands import certs


class CertsReloadTests(unittest.TestCase):
    def test_reload_validates_nginx_before_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_dir = Path(temp_dir).resolve()
            context = SimpleNamespace()
            args = Namespace(environment="dev", project_root=str(root_dir))

            with (
                patch.object(certs, "create_compose_context", return_value=context) as create_context,
                patch.object(certs, "run_compose") as run_compose,
            ):
                self.assertEqual(0, certs.cmd_reload(args))

        create_context.assert_called_once_with(root_dir, "dev", ensure_generated=False)
        self.assertEqual(
            [
                call(context, "exec", "-T", "nginx", "nginx", "-t"),
                call(context, "exec", "-T", "nginx", "nginx", "-s", "reload"),
            ],
            run_compose.mock_calls,
        )


if __name__ == "__main__":
    unittest.main()
