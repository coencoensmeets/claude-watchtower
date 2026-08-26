"""Which paths the panel will open, and which it will not.

/api/editor is the one route that takes a path from the browser — it is how a
path clicked out of a conversation is opened. That makes the fence around it
worth pinning down: the path must exist, and it must be inside your home folder
or the session's own. See watchtower.http._resolve_under.

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

from watchtower.http import _resolve_under  # noqa: E402


class ResolveUnder(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.work = self.home / "project"
        (self.work / "web").mkdir(parents=True)
        (self.work / "web" / "main.ts").write_text("x")
        self.outside = Path(self.tmp.name) / "elsewhere"
        self.outside.mkdir()
        (self.outside / "secret").write_text("x")
        # HOME is read at import time into a module global, so it is patched
        # where the resolver actually looks.
        import watchtower.http as http
        self._home_was = http.HOME
        http.HOME = self.home
        self.addCleanup(setattr, http, "HOME", self._home_was)
        self.addCleanup(self.tmp.cleanup)

    def resolve(self, raw: str, cwd: str | None = None):
        """The path it settled on, or None — the reason is checked apart."""
        spot, _ = _resolve_under(raw, cwd if cwd is not None else str(self.work), "")
        return spot

    def why(self, raw: str):
        return _resolve_under(raw, str(self.work), "")[1]

    def test_a_file_in_the_session_folder(self) -> None:
        self.assertEqual(self.resolve("web/main.ts"), (self.work / "web" / "main.ts").resolve())

    def test_an_absolute_path_under_home(self) -> None:
        self.assertEqual(self.resolve(str(self.work / "web")), (self.work / "web").resolve())

    def test_the_home_folder_itself(self) -> None:
        self.assertEqual(self.resolve(str(self.home)), self.home.resolve())

    def test_a_path_outside_both_is_refused(self) -> None:
        self.assertIsNone(self.resolve(str(self.outside / "secret")))

    def test_climbing_out_with_dot_dot_is_refused(self) -> None:
        self.assertIsNone(self.resolve("../../elsewhere/secret"))

    def test_a_symlink_pointing_out_is_refused(self) -> None:
        link = self.work / "way-out"
        try:
            link.symlink_to(self.outside / "secret")
        except OSError:                      # a filesystem without symlinks
            self.skipTest("no symlinks here")
        self.assertIsNone(self.resolve("way-out"))

    def test_something_that_is_not_there_is_refused(self) -> None:
        self.assertIsNone(self.resolve("web/gone.ts"))

    def test_the_two_refusals_are_told_apart(self) -> None:
        # "Not there" is about the path — a message older than the file it names
        # — and "outside" is about the panel. Reading one for the other sends
        # somebody looking for a file that is sitting where they left it.
        self.assertIn("Nothing is there", self.why("web/gone.ts"))
        self.assertIn("outside", self.why(str(self.outside / "secret")))

    def test_a_relative_path_with_no_session_folder_is_refused(self) -> None:
        self.assertIsNone(self.resolve("web/main.ts", cwd=""))

    def test_rubbish_is_refused(self) -> None:
        self.assertIsNone(self.resolve(""))
        self.assertIsNone(self.resolve("web/\x00main.ts"))
        self.assertIsNone(self.resolve("w" * 5000))

    def test_a_tilde_is_the_real_home_not_the_session_folder(self) -> None:
        # expanduser reads the environment's HOME rather than the panel's idea
        # of it, so the two are lined up here before the claim is made.
        os.environ["HOME"] = str(self.home)
        (self.home / "notes.md").write_text("x")
        self.assertEqual(self.resolve("~/notes.md"), (self.home / "notes.md").resolve())


if __name__ == "__main__":
    unittest.main()
