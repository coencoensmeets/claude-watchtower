"""Telling a headless run from a session in a terminal."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower import store  # noqa: E402


class Headless(unittest.TestCase):
    def test_a_terminal_session_writing_sdk_cli_is_watched(self):
        # Claude Code 2.1.325 writes `sdk-cli` for a plain `claude` in a terminal.
        with mock.patch.object(store, "stdin_is_terminal", lambda pid: True):
            self.assertFalse(store.is_headless({"pid": 1, "entrypoint": "sdk-cli"}))

    def test_a_piped_sdk_run_is_headless(self):
        with mock.patch.object(store, "stdin_is_terminal", lambda pid: False):
            self.assertTrue(store.is_headless({"pid": 1, "entrypoint": "sdk-cli"}))
            self.assertTrue(store.is_headless({"entrypoint": "sdk-ts"}))

    def test_a_cli_entrypoint_is_never_headless(self):
        with mock.patch.object(store, "stdin_is_terminal", lambda pid: False):
            self.assertFalse(store.is_headless({"pid": 1, "entrypoint": "cli"}))
            self.assertFalse(store.is_headless({"pid": 1}))


if __name__ == "__main__":
    unittest.main()
