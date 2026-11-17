"""+ New → Describe it…: Claude's answer about where work should run, and the
checks that keep it inside your home. The model is a fake; nothing runs.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower import route  # noqa: E402


class Fixture(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp()) / "home"
        (self.home / "code" / "robot-app" / ".git").mkdir(parents=True)
        (self.home / "code" / "robot-app" / "README.md").write_text("# Robot app\n\nJoint control for the arm.\n")
        (self.home / "code" / "site" / ".git").mkdir(parents=True)
        (self.home / "notes.txt").write_text("x")
        self.asked = []
        for p in (mock.patch.object(route, "HOME", self.home),
                  mock.patch.object(route, "PROJECT_DIR", self.home / ".claude" / "projects")):
            p.start()
            self.addCleanup(p.stop)

    def answer(self, **reply):
        def fake(prompt):
            self.asked.append(prompt)
            return True, "Sure:\n" + json.dumps(reply)
        return mock.patch.object(route, "ASK", fake)


class Known(Fixture):
    def test_finds_git_checkouts_with_what_they_are(self):
        found = {f["shown"]: f for f in route.known_folders()}
        self.assertIn("~/code/robot-app", found)
        self.assertEqual(found["~/code/robot-app"]["about"], "Robot app")
        self.assertEqual(route.projects_root(list(found.values())), str(self.home / "code"))


class Deciding(Fixture):
    def test_open_an_existing_project(self):
        with self.answer(action="open", folder="~/code/robot-app", reason="It is the robot app", title="Fix login"):
            plan, why = route.decide("fix the flaky test in the robot app")
        self.assertEqual(why, "")
        self.assertEqual(plan["action"], "open")
        self.assertEqual(plan["folder"], str((self.home / "code" / "robot-app").resolve()))
        self.assertIn("~/code/robot-app", self.asked[0])
        self.assertIn("Robot app", self.asked[0])

    def test_create_names_a_clean_free_folder_under_the_projects_root(self):
        with self.answer(action="create", folder="~/code/My Landing Page!", setup="Vite", title="Landing page"):
            plan, _ = route.decide("make a landing page")
        self.assertEqual(plan["action"], "create")
        self.assertEqual(plan["folder"], str(self.home / "code" / "my-landing-page"))
        self.assertEqual(plan["shown"], "~/code/my-landing-page")
        self.assertFalse(Path(plan["folder"]).exists(), "deciding must not make anything")

    def test_create_onto_an_existing_folder_becomes_open(self):
        with self.answer(action="create", folder="~/code/site"):
            plan, _ = route.decide("a site")
        self.assertEqual(plan["action"], "open")

    def test_refusals(self):
        for reply in ({"action": "open", "folder": "/etc"},
                      {"action": "create", "folder": "/tmp/evil"},
                      {"action": "open", "folder": "~/code/nope"},
                      {"action": "delete", "folder": "~"}):
            with self.answer(**reply):
                plan, why = route.decide("anything")
            self.assertIsNone(plan, reply)
            self.assertTrue(why)
        with mock.patch.object(route, "ASK", lambda p: (True, "I think robot-app")):
            self.assertIsNone(route.decide("x")[0])
        self.assertIsNone(route.decide("   ")[0])


class Starting(Fixture):
    def test_make_folder_is_fenced(self):
        made, _ = route.make_folder(str(self.home / "code" / "new-thing"))
        self.assertTrue(Path(made).is_dir())
        self.assertIsNone(route.make_folder("/tmp/outside-home")[0])
        self.assertIsNone(route.make_folder(str(self.home / "no" / "such" / "parent"))[0])
        self.assertIsNone(route.make_folder(str(self.home / "notes.txt"))[0])

    def test_first_message_sets_a_new_project_up_first(self):
        self.assertEqual(route.first_message("fix it", {"action": "open"}), "fix it")
        said = route.first_message("make a page", {"action": "create", "setup": "Vite + TS"})
        self.assertIn("Set the project up first (Vite + TS)", said)
        self.assertTrue(said.endswith("make a page"))


class StartPins(Fixture):
    def test_a_described_session_is_pinned_and_sent_the_request(self):
        import threading
        import urllib.request
        from watchtower import config
        from watchtower import http as web
        pinned, said = {}, []
        patches = [
            mock.patch.object(config, "SAY_ENABLED", True),
            mock.patch.object(web, "HOME", self.home),
            mock.patch.object(web, "owned_new", lambda cwd, mode, boxed=False: ("sid-1", "Started")),
            mock.patch.object(web, "owned_say", lambda sid, cwd, text, mode: (said.append((sid, cwd, text)), (True, "ok"))[1]),
            mock.patch.object(web, "pin_row", lambda sid, entry: pinned.__setitem__(sid, entry)),
            mock.patch.object(web, "kept_rows", lambda: {}),
            mock.patch.object(web, "load_owned", lambda: {}),
            mock.patch.object(web, "save_owned", lambda data: None),
            mock.patch.object(web, "load_names", lambda: {}),
            mock.patch.object(web, "save_names", lambda data: None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        body = json.dumps({"text": "make a page", "action": "create",
                           "folder": str(self.home / "code" / "page"), "title": "Page"}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/api/route/start", body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as r:
            out = json.loads(r.read())
        self.assertTrue(out["ok"], out)
        self.assertTrue((self.home / "code" / "page").is_dir())
        self.assertEqual(pinned["sid-1"]["cwd"], str((self.home / "code" / "page").resolve()))
        self.assertIn("make a page", said[0][2])


if __name__ == "__main__":
    unittest.main()
