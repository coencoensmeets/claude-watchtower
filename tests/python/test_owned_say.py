"""/api/owned/say on a session something else is already running."""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower import config  # noqa: E402
from watchtower import http as web  # noqa: E402


class OwnedSayToARunningSession(unittest.TestCase):
    def setUp(self):
        self.later, self.held = [], []
        running = {"sessionId": "sid-1", "pid": 4242, "cwd": "/tmp"}
        patches = [
            mock.patch.object(config, "SAY_ENABLED", True),
            mock.patch.object(web.STORE, "raw", lambda sid: dict(running) if sid == "sid-1" else None),
            mock.patch.object(web, "session_listening", lambda data: False),
            mock.patch.object(web, "deliver_later",
                              lambda sid, text: (self.later.append((sid, text)), (True, "Queued"))[1]),
            mock.patch.object(web, "owned_say", lambda *a: (self.held.append(a), (True, "ok"))[1]),
            mock.patch.object(web, "owned_hold", lambda *a: (self.held.append(a), (True, "ok"))[1]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def post(self, payload):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/owned/say",
                                     json.dumps({"sessionId": "sid-1", **payload}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_a_message_is_sent_to_it_rather_than_refused(self):
        status, out = self.post({"text": "carry on"})
        self.assertEqual(status, 200, out)
        self.assertEqual(self.later, [("sid-1", "carry on")])
        self.assertEqual(self.held, [], "the panel must not start a second process on it")

    def test_starting_it_up_is_still_refused(self):
        status, out = self.post({"text": ""})
        self.assertEqual(status, 409, out)
        self.assertIn("already running", out["message"])
        self.assertEqual(self.held, [])


if __name__ == "__main__":
    unittest.main()
