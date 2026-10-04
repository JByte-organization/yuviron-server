from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from commands.tools import _docker_clean_commands
from core.validators import CommandError


def docker_clean_args(**overrides: object) -> Namespace:
    values = {
        "mode": "report",
        "volumes": False,
        "reserved_space": "",
        "max_used_space": "",
        "min_free_space": "",
    }
    values.update(overrides)
    return Namespace(**values)


class DockerCleanCommandTests(unittest.TestCase):
    def test_build_cache_mode_keeps_reserved_cache_by_default(self) -> None:
        self.assertEqual(
            [["docker", "builder", "prune", "-f", "--reserved-space", "10gb"]],
            _docker_clean_commands(docker_clean_args(mode="build-cache")),
        )

    def test_safe_mode_prunes_non_running_data_and_build_cache(self) -> None:
        self.assertEqual(
            [
                ["docker", "container", "prune", "-f"],
                ["docker", "image", "prune", "-f"],
                ["docker", "network", "prune", "-f"],
                ["docker", "builder", "prune", "-f", "--reserved-space", "10gb"],
            ],
            _docker_clean_commands(docker_clean_args(mode="safe")),
        )

    def test_deep_mode_can_prune_anonymous_volumes_explicitly(self) -> None:
        self.assertEqual(
            [
                ["docker", "system", "prune", "-a", "-f"],
                ["docker", "builder", "prune", "-a", "-f"],
                ["docker", "volume", "prune", "-f"],
            ],
            _docker_clean_commands(docker_clean_args(mode="deep", volumes=True)),
        )

    def test_volumes_are_not_allowed_for_build_cache_only(self) -> None:
        with self.assertRaises(CommandError):
            _docker_clean_commands(docker_clean_args(mode="build-cache", volumes=True))


if __name__ == "__main__":
    unittest.main()
