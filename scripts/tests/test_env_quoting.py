"""Parametrized roundtrip tests for _quote_env_value / _unquote_env_value."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from core.compose_generator import _quote_env_value
from core.env import _unquote_env_value


ROUNDTRIP_CASES = [
    ("simple value",        "hello"),
    ("empty string",        ""),
    ("spaces",              "default-src 'self'; script-src 'self' blob:"),
    ("double quotes",       'say "hello"'),
    ("single quotes",       "it's fine"),
    ("dollar sign",         "price is $100"),
    ("backslash",           "C:\\Users\\test"),
    ("backslash+dollar",    "cost \\$50"),
    ("escaped sequence",    '\\"already escaped\\"'),
    ("newline in value",    "line1\nline2"),
    ("equals sign",         "KEY=VALUE"),
    ("mixed specials",      'a="b\\c$d"'),
    ("leading quote",       '"starts with quote'),
    ("trailing quote",      'ends with quote"'),
    ("only double quotes",  '""'),
    ("only backslash",      "\\"),
    ("only dollar",         "$"),
]


class TestEnvQuotingRoundtrip(unittest.TestCase):
    def test_roundtrip(self):
        for label, original in ROUNDTRIP_CASES:
            with self.subTest(label):
                quoted = _quote_env_value(original)
                restored = _unquote_env_value(quoted)
                self.assertEqual(
                    restored,
                    original,
                    f"roundtrip failed for {label!r}: {original!r} → {quoted!r} → {restored!r}",
                )

    def test_quote_always_double_quoted(self):
        for label, value in ROUNDTRIP_CASES:
            with self.subTest(label):
                quoted = _quote_env_value(value)
                self.assertTrue(quoted.startswith('"') and quoted.endswith('"'))

    def test_unquote_unquoted_value_unchanged(self):
        """Values without surrounding quotes are returned as-is."""
        self.assertEqual(_unquote_env_value("plain"), "plain")
        self.assertEqual(_unquote_env_value(""), "")
        self.assertEqual(_unquote_env_value("no quotes here"), "no quotes here")

    def test_unquote_single_quoted(self):
        self.assertEqual(_unquote_env_value("'hello world'"), "hello world")

    def test_dollar_not_expanded(self):
        original = "price $100"
        quoted = _quote_env_value(original)
        self.assertIn("\\$", quoted)
        self.assertEqual(_unquote_env_value(quoted), original)

    def test_backslash_not_doubled(self):
        original = "C:\\path"
        quoted = _quote_env_value(original)
        self.assertIn("\\\\", quoted)
        self.assertEqual(_unquote_env_value(quoted), original)


if __name__ == "__main__":
    unittest.main()
