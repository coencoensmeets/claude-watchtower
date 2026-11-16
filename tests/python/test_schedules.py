"""Scheduled tasks: the clock, the runs, the routes and the MCP tools.

Command tasks really run (`bash -lc` in a temporary folder). Claude tasks have
their runner swapped out: nothing here starts a `claude`.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower import config, schedules  # noqa: E402
from watchtower import http as web  # noqa: E402
from watchtower.mcp import Tools, ToolError, parse_moment, parse_span  # noqa: E402

SID = "bbbbbbbb-0000-0000-0000-000000000000"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.proj = self.home / "proj"
        self.proj.mkdir(parents=True)
        for p in (mock.patch.object(schedules, "SCHEDULE_FILE", self.tmp / "schedules.json"),
                  mock.patch.object(config, "SAY_ENABLED", True)):
            p.start()
            self.addCleanup(p.stop)
        schedules.reset_for_tests()
        self.addCleanup(schedules.reset_for_tests)

    def add(self, **fields):
        task, why = schedules.save_task({"cwd": str(self.proj), **fields})
        self.assertIsNotNone(task, why)
        return task


class Clock(Base):
    def test_repeating_keeps_its_rhythm_and_skips_what_it_missed(self):
        task = {"at": 1000.0, "every": 600}
        self.assertEqual(schedules.next_after(task, 500), 1000)
        self.assertEqual(schedules.next_after(task, 1000), 1600)
        # Down for an hour: the next moment still ahead, on the same grid.
        self.assertEqual(schedules.next_after(task, 1000 + 3600 + 5), 1000 + 3600 + 600)
        self.assertIsNone(schedules.next_after({"at": 1000.0}, 2000))

    def test_a_command_runs_and_its_output_is_kept(self):
        task = self.add(kind="command", text="echo hello from $(basename $PWD); exit 3", at=time.time() - 1)
        self.assertEqual(schedules.tick(wait=True), [task["id"]])
        done = schedules.get(task["id"])
        self.assertFalse(done["enabled"], "a one-off is done once it has run")
        self.assertIsNone(done["next"])
        self.assertEqual(done["last"]["exit"], 3)
        self.assertFalse(done["last"]["ok"])
        self.assertIn("hello from proj", done["last"]["output"])
        self.assertEqual(done["runs"], 1)
        self.assertEqual(schedules.tick(wait=True), [])

    def test_a_repeating_task_moves_on_and_stays_on(self):
        # The panel was down for the first three: one run, then back on the grid.
        start = time.time() + 100
        task = self.add(kind="command", text="true", at=start, every=3600)
        self.assertEqual(schedules.tick(wait=True), [])
        self.assertEqual(schedules.tick(now=start + 3 * 3600 + 10, wait=True), [task["id"]])
        after = schedules.get(task["id"])
        self.assertTrue(after["enabled"])
        self.assertTrue(after["last"]["ok"])
        self.assertAlmostEqual(after["next"], start + 4 * 3600, places=3)

    def test_nothing_runs_while_sending_is_off(self):
        task = self.add(kind="command", text=f"touch {self.proj}/ran", at=time.time() - 1)
        with mock.patch.object(config, "SAY_ENABLED", False):
            self.assertEqual(schedules.tick(wait=True), [])
        self.assertFalse((self.proj / "ran").exists())
        self.assertIn("Skipped", schedules.get(task["id"])["last"]["message"])

    def test_a_command_past_its_timeout_is_stopped(self):
        task = self.add(kind="command", text="sleep 30", at=time.time() - 1)
        # The floor is ten seconds; set it under the floor directly for speed.
        schedules._load()[0]["timeout"] = 1
        began = time.time()
        schedules.tick(wait=True)
        self.assertLess(time.time() - began, 15)
        self.assertIn("Stopped after", schedules.get(task["id"])["last"]["message"])

    def test_a_claude_task_on_the_same_session_remembers_it(self):
        sent = []

        def fake(task):
            sent.append(task.get("sessionId"))
            return {"at": time.time(), "ok": True, "sessionId": task.get("sessionId") or "new-sid",
                    "message": "Sent"}

        with mock.patch.dict(schedules.RUNNERS, {"claude": fake}):
            task = self.add(kind="claude", text="Summarise the open issues", session="same",
                            at=time.time(), every=3600)
            schedules.tick(wait=True)
            schedules.start_run(task["id"], wait=True)
        self.assertEqual(sent, [None, "new-sid"])
        self.assertEqual(schedules.get(task["id"])["sessionId"], "new-sid")

    def test_refusals(self):
        for fields, word in (({"kind": "nope", "text": "x"}, "one of"),
                             ({"kind": "command", "text": ""}, "Say what"),
                             ({"kind": "command", "text": "x", "every": 5}, "once a minute"),
                             ({"kind": "claude", "text": "x", "mode": "yolo"}, "Mode")):
            task, why = schedules.save_task({"cwd": str(self.proj), **fields})
            self.assertIsNone(task)
            self.assertIn(word, why)

    def test_pause_and_resume(self):
        task = self.add(kind="command", text="true", at=time.time() + 3600, every=3600)
        self.assertIsNone(schedules.set_enabled(task["id"], False)["next"])
        self.assertGreater(schedules.set_enabled(task["id"], True)["next"], time.time())


class FakeStore:
    def __init__(self, cwd):
        self.cwd = cwd

    def snapshot(self):
        return {"sessions": [{"sessionId": SID, "name": "proj", "cwd": self.cwd, "repoRoot": None}]}

    def raw(self, _sid):
        return None


class Routes(Base):
    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(web, "STORE", FakeStore(str(self.proj))),
                  mock.patch.object(web, "kept_rows", lambda: {}),
                  mock.patch.object(web, "HOME", self.home),
                  mock.patch.object(config, "ACCESS_KEY", "sekrit")):
            p.start()
            self.addCleanup(p.stop)
        self.server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_add_list_pause_remove(self):
        code, said = self.call("POST", "/api/schedules", {"sessionId": SID, "kind": "command",
                                                          "text": "make test", "every": 3600})
        self.assertEqual(code, 200, said)
        tid = said["task"]["id"]
        self.assertEqual(said["task"]["cwd"], str(self.proj))
        _, state = self.call("GET", "/api/state")
        self.assertEqual([t["id"] for t in state["schedules"]], [tid])
        self.assertNotIn("history", state["schedules"][0])
        _, listed = self.call("GET", f"/api/schedules?sessionId={SID}")
        self.assertEqual(len(listed["tasks"]), 1)
        code, said = self.call("POST", "/api/schedules/enable", {"id": tid, "enabled": False})
        self.assertFalse(said["task"]["enabled"])
        code, said = self.call("POST", "/api/schedules", {"id": tid, "title": "Tests"})
        self.assertEqual((code, said["task"]["title"], said["task"]["text"]), (200, "Tests", "make test"))
        self.assertEqual(self.call("POST", "/api/schedules/remove", {"id": tid})[0], 200)
        self.assertEqual(self.call("POST", "/api/schedules/remove", {"id": tid})[0], 404)

    def test_a_star_is_kept_through_an_edit_and_carried_in_the_feed(self):
        _, said = self.call("POST", "/api/schedules", {"sessionId": SID, "kind": "command", "text": "ls"})
        tid = said["task"]["id"]
        self.assertFalse(said["task"]["starred"])
        code, said = self.call("POST", "/api/schedules/star", {"id": tid, "starred": True})
        self.assertEqual((code, said["task"]["starred"]), (200, True))
        _, said = self.call("POST", "/api/schedules", {"id": tid, "title": "Listing"})
        self.assertTrue(said["task"]["starred"], "editing a task must not drop its star")
        _, state = self.call("GET", "/api/state")
        self.assertTrue(state["schedules"][0]["starred"])
        _, said = self.call("POST", "/api/schedules/star", {"id": tid, "starred": False})
        self.assertFalse(said["task"]["starred"])
        self.assertEqual(self.call("POST", "/api/schedules/star", {"id": "nope"})[0], 404)

    def test_a_folder_outside_home_is_refused(self):
        code, said = self.call("POST", "/api/schedules", {"cwd": "/etc", "kind": "command", "text": "ls"})
        self.assertEqual(code, 400)
        self.assertIn("home", said["message"])

    def test_off_when_sending_is_off(self):
        with mock.patch.object(config, "SAY_ENABLED", False):
            code, _ = self.call("POST", "/api/schedules", {"sessionId": SID, "kind": "command", "text": "ls"})
        self.assertEqual(code, 403)


class Mcp(unittest.TestCase):
    def test_spans_and_moments(self):
        self.assertEqual(parse_span("1h30m"), 5400)
        self.assertEqual(parse_span("2 days"), 172800)
        self.assertEqual(parse_span(15), 900)
        with self.assertRaises(ToolError):
            parse_span("fortnightly-ish")
        now = datetime(2026, 11, 16, 15, 0).timestamp()
        self.assertEqual(parse_moment("in 10m", now), now + 600)
        self.assertEqual(parse_moment("14:30", now), datetime(2026, 11, 17, 14, 30).timestamp())
        self.assertEqual(parse_moment("2026-11-17T09:00", now), datetime(2026, 11, 17, 9, 0).timestamp())

    def test_schedule_task_goes_to_this_sessions_folder(self):
        calls = []

        def call(method, path, body):
            calls.append((method, path, body))
            if path == "/api/state":
                return {"sessions": [{"sessionId": SID, "name": "proj", "cwd": "/x"}]}
            return {"task": {"id": "t1", "title": "t", "kind": "claude", "cwd": "/x", "text": "hi",
                             "every": 3600, "next": time.time() + 60, "enabled": True}}

        tools = Tools(call=call, parents=[], env={"CLAUDE_CODE_SESSION_ID": SID})
        out = tools.schedule_task(prompt="hi", every="1h")
        posted = [c for c in calls if c[1] == "/api/schedules"][0][2]
        self.assertEqual((posted["kind"], posted["every"], posted["sessionId"]), ("claude", 3600, SID))
        self.assertEqual(out["task"]["repeats"], "every hour")
        with self.assertRaises(ToolError):
            tools.schedule_task(prompt="a", command="b")


if __name__ == "__main__":
    unittest.main()
