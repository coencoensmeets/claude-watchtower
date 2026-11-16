"""Previews: a session showing you a page it made, and the panel serving it.

The routes are driven through the real Handler on a throwaway loopback port,
with the session store, the kept rows and the previews file swapped for
fakes — not a second panel: nothing here resumes or holds a session.

    python3 -m unittest discover -s tests/python
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from watchtower import config, previews  # noqa: E402
from watchtower import http as web  # noqa: E402
from watchtower.mcp import Tools  # noqa: E402

SID = "aaaaaaaa-0000-0000-0000-000000000000"


class FakeStore:
    def __init__(self, cwd):
        self.cwd = cwd

    def snapshot(self):
        return {"sessions": [{"sessionId": SID, "name": "site", "cwd": self.cwd, "repoRoot": None}]}


class Routes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.site = self.home / "proj" / "styleguide"
        (self.site / "assets").mkdir(parents=True)
        (self.site / "index.html").write_text('<link rel="stylesheet" href="assets/s.css"><h1>Hi</h1>')
        (self.site / "assets" / "s.css").write_text("h1{color:red}")
        (self.home / "proj" / "secret.txt").write_text("nope")
        (self.home / "loose.html").write_text("<p>loose</p>")
        patches = [
            mock.patch.object(previews, "PREVIEW_FILE", self.tmp / "previews.json"),
            mock.patch.object(previews, "STAR_FILE", self.tmp / "starred.json"),
            mock.patch.object(web, "STORE", FakeStore(str(self.home / "proj"))),
            mock.patch.object(web, "kept_rows", lambda: {}),
            mock.patch.object(web, "HOME", self.home),
            mock.patch.object(config, "SAY_ENABLED", True),
            mock.patch.object(config, "ACCESS_KEY", "sekrit"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def write(self, rel):
        spot = self.home / rel
        spot.write_text("x = 1")
        return spot

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_a_folder_is_shown_and_served_sandboxed(self):
        code, said = self.post("/api/previews", {"sessionId": SID, "target": str(self.site), "title": "Styleguide"})
        self.assertEqual(code, 200, said)
        self.assertEqual(said["preview"]["title"], "Styleguide")
        code, headers, body = self.get(said["path"])
        self.assertEqual(code, 200)
        self.assertIn(b"<h1>Hi</h1>", body)
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        self.assertNotIn("allow-same-origin", headers["Content-Security-Policy"])
        token = said["preview"]["token"]
        code, headers, body = self.get(f"/preview/{token}/assets/s.css")
        self.assertEqual((code, body), (200, b"h1{color:red}"))
        self.assertTrue(headers["Content-Type"].startswith("text/css"))

    def test_a_page_carries_the_commenting_script_and_nothing_else_does(self):
        """Selecting a passage to comment on needs the page to say what was
        selected, so HTML gets the script — once, after the page's own markup —
        and a stylesheet or any other file is served exactly as it is on disk."""
        _, said = self.post("/api/previews", {"sessionId": SID, "target": str(self.site)})
        _, _, body = self.get(said["path"])
        self.assertEqual(body.count(b"<script data-wt-pin>"), 1)
        self.assertTrue(body.startswith(b'<link rel="stylesheet" href="assets/s.css"><h1>Hi</h1>'))
        self.assertIn(b"sel-take", body)
        token = said["preview"]["token"]
        self.assertEqual(self.get(f"/preview/{token}/assets/s.css")[2], b"h1{color:red}")

    def test_a_fragment_with_no_body_still_gets_the_script(self):
        self.assertTrue(previews.with_pin_script(b"<p>hi</p>").startswith(b"<p>hi</p><script data-wt-pin>"))
        out = previews.with_pin_script(b"<BODY>x</BODY>")
        self.assertTrue(out.endswith(b"</script></BODY>"))

    def test_nothing_outside_the_folder_and_no_guessing(self):
        _, said = self.post("/api/previews", {"sessionId": SID, "target": str(self.site / "index.html")})
        token = said["preview"]["token"]
        for path in (f"/preview/{token}/../secret.txt", f"/preview/{token}/%2e%2e/secret.txt",
                     "/preview/wrong-token/index.html"):
            self.assertEqual(self.get(path)[0], 404, path)

    def test_the_feed_carries_them_and_showing_again_keeps_the_address(self):
        _, first = self.post("/api/previews", {"sessionId": SID, "target": str(self.site)})
        _, again = self.post("/api/previews", {"sessionId": SID, "target": str(self.site / "index.html")})
        self.assertEqual(first["preview"]["token"], again["preview"]["token"])
        self.assertEqual(again["preview"]["shown"], 2)
        self.assertEqual(len(previews.read_previews()[SID]), 1)

    def test_refusals(self):
        cases = [
            {"target": "/etc/hostname"},                        # outside home
            {"target": str(self.home / "loose.html")},          # would serve all of home
            {"target": str(self.write("proj/tool.py"))},          # not a page
            {"target": "ftp://example.com"},                    # not http(s)
        ]
        for body in cases:
            code, said = self.post("/api/previews", {"sessionId": SID, **body})
            self.assertNotEqual(code, 200, body)
            self.assertFalse(said["ok"])

    def test_a_url_is_kept_and_removed(self):
        code, said = self.post("/api/previews", {"sessionId": SID, "target": "http://localhost:5173/"})
        self.assertEqual(code, 200)
        self.assertNotIn("token", said["preview"])
        code, _ = self.post("/api/previews/remove", {"sessionId": SID, "id": said["preview"]["id"]})
        self.assertEqual(code, 200)
        self.assertEqual(previews.read_previews(), {})

    def test_a_star_outlives_the_sessions_preview(self):
        _, said = self.post("/api/previews", {"sessionId": SID, "target": str(self.site), "title": "Guide"})
        row = said["preview"]
        code, starred = self.post("/api/previews/star", {"sessionId": SID, "id": row["id"]})
        self.assertEqual(code, 200, starred)
        star = starred["star"]
        self.assertEqual((star["title"], star["from"], star["cwd"]), ("Guide", SID, str(self.home / "proj")))
        # Starring it again is the same star.
        _, again = self.post("/api/previews/star", {"sessionId": SID, "id": row["id"]})
        self.assertEqual(again["star"]["id"], star["id"])
        self.post("/api/previews/remove", {"sessionId": SID, "id": row["id"]})
        self.assertEqual(previews.read_previews(), {})
        # Still listed on the feed, and still served at the address it had.
        _, _, body = self.get("/api/state")
        self.assertEqual([s["id"] for s in json.loads(body)["starred"]], [star["id"]])
        code, _, body = self.get(f"/preview/{star['token']}/index.html")
        self.assertEqual((code, b"<h1>Hi</h1>" in body), (200, True))
        code, _ = self.post("/api/starred/rename", {"id": star["id"], "title": "  The  guide "})
        self.assertEqual((code, previews.read_starred()[0]["title"]), (200, "The guide"))
        code, _ = self.post("/api/starred/remove", {"id": star["id"]})
        self.assertEqual((code, previews.read_starred()), (200, []))
        self.assertEqual(self.get(f"/preview/{star['token']}/index.html")[0], 404)
        self.assertEqual(self.post("/api/previews/star", {"sessionId": SID, "id": "nope"})[0], 404)


class Tool(unittest.TestCase):
    def test_show_preview_names_this_session_and_hands_back_a_url(self):
        calls = []

        def panel(method, path, body):
            calls.append((path, body))
            if path == "/api/state":
                return {"sessions": [{"sessionId": SID, "name": "site", "cwd": "/h"}], "owned": {}}
            return {"ok": True, "preview": {"title": "Styleguide", "kind": "file"},
                    "path": "/preview/tok/index.html"}

        tools = Tools(call=panel, parents=[], env={"CLAUDE_CODE_SESSION_ID": SID})
        with mock.patch("watchtower.mcp.panel_port", return_value=8787):
            out = tools.show_preview("/h/site/index.html", "Styleguide")
        self.assertEqual(calls[-1][1]["sessionId"], SID)
        self.assertEqual(out["url"], "http://127.0.0.1:8787/preview/tok/index.html")


if __name__ == "__main__":
    unittest.main()
