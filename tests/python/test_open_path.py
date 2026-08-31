"""Which paths the panel will open, and which it will not.

/api/editor and /api/file are the two routes that take a path from the browser —
one opens what was clicked out of a conversation, the other serves a picture a
message names. That makes the fence around them worth pinning down: the path
must exist, and it must be inside your home folder or the session's own. See
watchtower.http._resolve_under.

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


class WhatOpensWhat(unittest.TestCase):
    """A file goes to the editor, a folder to the desktop.

    The command line is asserted rather than run: these would otherwise open
    windows on whoever's machine is running the tests.
    """

    def setUp(self) -> None:
        import watchtower.control as control
        self.control = control
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "build"
        self.dir.mkdir()
        self.file = self.dir / "main.ts"
        self.file.write_text("x")
        self.ran: list[list[str]] = []

        def popen(argv, **kwargs):
            self.ran.append(list(argv))
            return None

        self._popen_was = control.subprocess.Popen
        control.subprocess.Popen = popen
        self._which_was = control.shutil.which
        control.shutil.which = lambda name: f"/usr/bin/{name}"
        os.environ.pop("CLAUDE_WATCHTOWER_EDITOR", None)
        self.addCleanup(setattr, control.subprocess, "Popen", self._popen_was)
        self.addCleanup(setattr, control.shutil, "which", self._which_was)
        self.addCleanup(self.tmp.cleanup)

    def test_a_file_goes_to_the_editor_at_its_line(self) -> None:
        ok, said = self.control.open_editor(str(self.file), 44)
        self.assertTrue(ok, said)
        self.assertEqual(self.ran, [["/usr/bin/code", "--goto", f"{self.file}:44"]])

    def test_a_file_with_no_line_still_goes_through_goto(self) -> None:
        self.control.open_editor(str(self.file))
        self.assertEqual(self.ran, [["/usr/bin/code", "--goto", str(self.file)]])

    def test_a_folder_the_button_named_goes_to_the_editor_bare(self) -> None:
        # The header's own button, which says VS Code on it.
        self.control.open_editor(str(self.dir))
        self.assertEqual(self.ran, [["/usr/bin/code", str(self.dir)]])

    def test_a_folder_clicked_in_a_message_goes_to_the_desktop(self) -> None:
        ok, said = self.control.show_folder(str(self.dir))
        self.assertTrue(ok, said)
        self.assertEqual(self.ran, [["/usr/bin/xdg-open", str(self.dir)]])

    def test_the_desktop_opener_is_not_offered_a_file(self) -> None:
        ok, said = self.control.show_folder(str(self.file))
        self.assertFalse(ok)
        self.assertEqual(self.ran, [])

    def test_without_a_desktop_opener_a_folder_falls_back_to_the_editor(self) -> None:
        self.control.shutil.which = lambda name: None if name == "xdg-open" else f"/usr/bin/{name}"
        ok, said = self.control.show_folder(str(self.dir))
        self.assertTrue(ok, said)
        self.assertEqual(self.ran, [["/usr/bin/code", str(self.dir)]])
        self.assertIn("No desktop opener", said)

    def test_nothing_is_opened_that_is_not_there(self) -> None:
        self.assertFalse(self.control.open_editor(str(self.dir / "gone.ts"))[0])
        self.assertFalse(self.control.show_folder(str(self.dir / "gone"))[0])
        self.assertEqual(self.ran, [])


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

    def test_the_temp_folder_is_for_pictures_and_nothing_else(self) -> None:
        # A screenshot lands in /tmp by habit, so /api/file is allowed to read
        # one from there. /api/editor is not: it starts a process.
        import tempfile as tmp
        shot = Path(tmp.gettempdir()) / "watchtower-test-shot.png"
        shot.write_bytes(b"\x89PNG\r\n")
        self.addCleanup(shot.unlink, missing_ok=True)
        self.assertIsNone(_resolve_under(str(shot), str(self.work), "")[0])
        self.assertEqual(_resolve_under(str(shot), str(self.work), "", temp=True)[0], shot.resolve())

    def test_the_temp_folder_does_not_open_the_rest_of_the_disk(self) -> None:
        # Somewhere that is neither home, nor the session, nor temp. /etc is on
        # every machine this runs on and is nobody's screenshot folder.
        self.assertIsNone(_resolve_under("/etc/hostname", str(self.work), "", temp=True)[0])

    def test_a_tilde_is_the_real_home_not_the_session_folder(self) -> None:
        # expanduser reads the environment's HOME rather than the panel's idea
        # of it, so the two are lined up here before the claim is made.
        os.environ["HOME"] = str(self.home)
        (self.home / "notes.md").write_text("x")
        self.assertEqual(self.resolve("~/notes.md"), (self.home / "notes.md").resolve())


class PicturesOnly(unittest.TestCase):
    """What /api/file will hand over, beyond where it will look for it.

    The path fence is ResolveUnder's business. This is the second half: the
    route serves pictures, and a route that will hand over any readable file
    inside your home folder is a route for reading files.
    """

    def suffixes(self):
        from watchtower.http import Handler
        return Handler.PICTURES

    def test_the_kinds_a_message_can_show(self) -> None:
        for suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
            self.assertIn(suffix, self.suffixes())

    def test_and_the_kinds_it_cannot(self) -> None:
        for suffix in (".txt", ".py", ".json", ".pdf", ".zip", ".env", ".key", ""):
            self.assertNotIn(suffix, self.suffixes())

    def test_every_kind_names_an_image_type(self) -> None:
        for suffix, kind in self.suffixes().items():
            self.assertTrue(kind.startswith("image/"), f"{suffix} is {kind}")

    def test_there_is_a_ceiling_on_what_is_sent(self) -> None:
        from watchtower.http import Handler
        self.assertGreater(Handler.PICTURE_MAX, 1024 * 1024)
        self.assertLessEqual(Handler.PICTURE_MAX, 64 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
