"""What the settings page is told about *Open in terminal*.

Two things have to be true before the button can do anything — a terminal the
panel knows how to open, and `claude` on the PATH it would open it with — and
neither is about a session, so the answer is drawn once on the settings page.
See watchtower.control.terminal_report.

The PATH is moved rather than the machine: each case builds a folder holding
exactly the executables it is about, so the result does not depend on what the
machine running the tests happens to have installed.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from watchtower import control  # noqa: E402


class TerminalReport(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bin = Path(self.tmp.name)
        self.was = dict(os.environ)
        os.environ["PATH"] = str(self.bin)
        os.environ.pop("CLAUDE_WATCHTOWER_TERMINAL", None)
        os.environ.pop("CLAUDE_BUSY_UI_TERMINAL", None)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.was)))

    def put(self, *names: str) -> None:
        """Put executables on the PATH this test is looking at."""
        for name in names:
            path = self.bin / name
            path.write_text("#!/bin/sh\n")
            path.chmod(0o755)

    def test_a_terminal_and_a_claude_is_all_it_takes(self):
        self.put("xterm", "claude")
        report = control.terminal_report()
        self.assertTrue(report["can"])
        self.assertEqual(report["terminal"], "xterm")
        self.assertEqual(report["install"], "")

    def test_the_terminal_named_is_the_one_terminal_argv_would_open(self):
        # kitty is above xterm on the list, so it is the one reported when both
        # are installed — the settings page must not name a different terminal
        # from the one the button opens.
        self.put("kitty", "xterm", "claude")
        self.assertEqual(control.terminal_report()["terminal"], "kitty")

    def test_no_terminal_is_a_no_with_a_line_to_type(self):
        self.put("claude", "apt")
        report = control.terminal_report()
        self.assertFalse(report["can"])
        self.assertEqual(report["terminal"], "")
        self.assertEqual(report["install"], "sudo apt install xterm")

    def test_the_install_line_follows_the_package_manager_that_is_there(self):
        self.put("claude", "pacman")
        self.assertEqual(control.terminal_report()["install"], "sudo pacman -S xterm")

    def test_a_machine_with_no_manager_we_know_is_told_nothing_rather_than_a_guess(self):
        self.put("claude")
        self.assertEqual(control.terminal_report()["install"], "")

    def test_a_terminal_without_claude_still_cannot_do_it(self):
        self.put("xterm")
        report = control.terminal_report()
        self.assertFalse(report["can"])
        self.assertEqual(report["terminal"], "xterm")
        self.assertEqual(report["claude"], "")

    def test_an_override_is_the_only_terminal_looked_for(self):
        # The override names the terminal outright, so an xterm sitting on the
        # PATH beside it is not a fallback — terminal_argv would not open it.
        self.put("xterm", "claude", "apt")
        os.environ["CLAUDE_WATCHTOWER_TERMINAL"] = "kitty --"
        report = control.terminal_report()
        self.assertFalse(report["can"])
        self.assertEqual(report["named"], "kitty")
        # And nothing to install: the machine has a terminal, the variable is
        # what is wrong.
        self.assertEqual(report["install"], "")

    def test_an_override_that_is_there_is_taken(self):
        self.put("kitty", "claude")
        os.environ["CLAUDE_WATCHTOWER_TERMINAL"] = "kitty --"
        self.assertTrue(control.terminal_report()["can"])

    def test_the_old_name_for_the_override_is_still_read(self):
        self.put("kitty", "claude")
        os.environ["CLAUDE_BUSY_UI_TERMINAL"] = "kitty --"
        self.assertEqual(control.terminal_report()["named"], "kitty")


if __name__ == "__main__":
    unittest.main()
