from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from core.paths import compose_relative_path, resolve_runtime_path


class PathsTests(unittest.TestCase):
    def test_compose_relative_path_is_relative_to_infra_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "storage" / "dev"

            self.assertEqual("../storage/dev", compose_relative_path(root, path))

    def test_resolve_runtime_path_supports_compose_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            self.assertEqual(
                (root / "storage" / "dev").resolve(),
                resolve_runtime_path(root, "../storage/dev"),
            )

    def test_resolve_runtime_path_keeps_legacy_root_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            self.assertEqual(
                (root / "generated" / "dev" / "htpasswd").resolve(),
                resolve_runtime_path(root, "./generated/dev/htpasswd"),
            )


if __name__ == "__main__":
    unittest.main()
